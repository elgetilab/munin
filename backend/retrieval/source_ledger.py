"""
Per-conversation source ledger: the model cites by id, the server renders
(docs/agent-track/SOURCE-LEDGER-PLAN.md).

Every source a tool shows the model (a paper, a web page) gets a short id
(`S7`) and every artifact one too (`A2`), stable for the whole conversation and
persisted in chats.db. The model writes `[S7]`; after the turn, `render`
replaces the ids with numbered links and appends a References list built from
the stored records, so no DOI, author list, journal or artifact URL is typed
by the model. Later turns get a compact sources block instead of the raw tool
results, which are not replayed.

Why: wrong references came mostly from model memory and miscopying (a review
of 25 traced errors, 2026-11-24). When a tool supplied a DOI the model copied
it; what it invented was what the tools did not show or what had left the
context.
"""

from __future__ import annotations

import copy
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional
from urllib.parse import unquote

logger = logging.getLogger(__name__)

# Sources block budget: about 1,500 tokens (SOURCE-LEDGER-PLAN.md section 6).
BLOCK_MAX_CHARS = 6000
MAX_AUTHORS_SHOWN = 6

CITE_RULE = (
    "How to cite (this overrides any other instruction to include DOIs or links): every "
    "source a tool shows you carries an id (`sid`: S1, S2, ...) and every artifact one "
    "too (`cite_as`: [A1], ...). Cite a source as [S7], several as [S3, S7], an artifact "
    "as [A2]. Never type a DOI, a paper or artifact URL, an author-year citation or a "
    "reference list yourself: the server turns the ids into numbered links and a "
    "References list from the stored records. A list or table of papers carries the "
    "id where the reference or DOI would go (\"- [S3]: why it matters\", a table cell "
    "[S3]); the server fills in each checked reference. When the user asks for a reference, a "
    "citation or a DOI, answer with the id too (\"The paper is [S4].\"): the server "
    "inserts the full, checked reference. If no source you retrieved supports a "
    "claim, say so instead of citing.")
ARTIFACT_RULE = ("Cite an artifact by its `cite_as` id ([A2]); never type an artifact URL: "
                 "the server turns the id into a working link.")

_DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\"'<>\[\]`{}|\\]+", re.IGNORECASE)
_DOI_URL_RE = re.compile(r"^https?://(?:dx\.)?doi\.org/(10\.\d{4,9}/\S+)$", re.IGNORECASE)
# "[S7]", "[S3, S7]", "[S3; A2]"; also the full-width brackets some models use.
_CITE_RE = re.compile(r"[\[【]\s*((?:[SA]\d{1,4})(?:\s*[,;]\s*[SA]\d{1,4})*)\s*[\]】]")
_ID_RE = re.compile(r"[SA]\d{1,4}")
# A reference the model typed, rewritten by reconcile and filled in by render.
_PLACE_RE = re.compile(r"\x02(S\d{1,4})\x02")
_DOI_LINK_RE = re.compile(r"\[([^\]\n]*)\]\(https?://(?:dx\.)?doi\.org/(10\.\d{4,9}/[^)\s]+)\)", re.IGNORECASE)
_DOI_BARE_RE = re.compile(r"`?(?:https?://(?:dx\.)?doi\.org/)?(10\.\d{4,9}/[^\s\"'<>\[\]`{}|\\*]+)`?", re.IGNORECASE)
_LINK_WORDS = {"doi", "link", "here", "pdf", "full text", "paper", "article", "doi link"}
_YEAR_RE = re.compile(r"\b(?:19[5-9]\d|20[0-3]\d)\b")
# List markers and reference labels before a typed reference: "- ", "2. ",
# "**[5]** ", "[5] ", "(30) ", "(b) ", "[^5^]: ", "**Full reference:** ".
_REF_PREFIX_RE = re.compile(
    r"\s*(?:(?:[-*+]|\d{1,3}[.)])\s+|\*\*\[\d{1,3}\]\*\*\s*|\[\d{1,3}\]\s*|\(\w{1,3}\)\s*"
    r"|\[\^\w+\^?\]:\s*|\*\*[A-Z][\w ]{0,30}(?::\*\*|\*\*:)\s*|\[S\d{1,4}\]\s*)*")
_INITIALS_FIRST_RE = re.compile(r"^\**(?:[A-Z]\.[\s-]?){1,3}\s?[A-Z][\w'\-]+")


def enabled() -> bool:
    """The ledger is on unless MUNIN_SOURCE_LEDGER is off (0/off/false/no).
    On by default since the 2026-11-25 user-failure regression
    (scorecards/userfail/2026-11-25_full-*, 2026-11-26_mech-*)."""
    import os
    return (os.environ.get("MUNIN_SOURCE_LEDGER") or "on").strip().lower() not in ("0", "off", "false", "no")


def norm_doi(doi: str) -> str:
    d = (doi or "").strip()
    d = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", d, flags=re.IGNORECASE)
    return d.rstrip(".,;:)").lower()


def norm_url(url: str) -> str:
    u = (url or "").strip()
    u = u.split("#", 1)[0]
    u = re.sub(r"^https?://(www\.)?", "", u, flags=re.IGNORECASE)
    return u.rstrip("/").lower()


@dataclass
class Entry:
    sid: str
    kind: str            # paper | web | artifact | upload
    key: str             # doi:..., url:..., pid:..., art:...
    record: dict
    first_turn: int = 0
    dirty: bool = False


@dataclass
class Ledger:
    conversation_id: Optional[str] = None
    entries: dict = field(default_factory=dict)   # sid -> Entry
    by_key: dict = field(default_factory=dict)    # key -> sid
    rule_shown: bool = False                       # this turn's model has seen the citing rule

    # ------------------------------------------------------------------
    # Building
    # ------------------------------------------------------------------
    def _next(self, prefix: str) -> str:
        n = max([int(s[1:]) for s in self.entries if s[0] == prefix] or [0])
        return f"{prefix}{n + 1}"

    def add(self, kind: str, key: str, record: dict, turn: int = 0) -> str:
        """The id for this source, creating it on first sight. A later sighting
        fills fields the first one lacked (a search hit, then a full read)."""
        sid = self.by_key.get(key)
        if sid:
            e = self.entries[sid]
            for k, v in record.items():
                if v and not e.record.get(k):
                    e.record[k] = v
                    e.dirty = True
            return sid
        sid = self._next("A" if kind == "artifact" else "S")
        self.entries[sid] = Entry(sid, kind, key, dict(record), turn, dirty=True)
        self.by_key[key] = sid
        return sid

    @staticmethod
    def source_of(d: dict) -> Optional[tuple[str, str, dict]]:
        """(kind, key, record) when a tool-result dict describes a source."""
        if not isinstance(d, dict):
            return None
        title = d.get("title") if isinstance(d.get("title"), str) else None
        doi = d.get("doi") if isinstance(d.get("doi"), str) else None
        url = d.get("url") if isinstance(d.get("url"), str) else None
        if url and not doi:
            m = _DOI_URL_RE.match(url.strip())
            if m:
                doi = m.group(1)
        rec = {k: d.get(k) for k in ("title", "authors", "year", "journal", "venue", "doi", "url",
                                     "paper_id", "source_type") if d.get(k)}
        if doi and _DOI_RE.match(norm_doi(doi)):   # a DOI field may hold a doi.org URL
            rec["doi"] = norm_doi(doi)
            return "paper", "doi:" + rec["doi"], rec
        if url and title and url.startswith(("http://", "https://")):
            return "web", "url:" + norm_url(url), rec
        pid = d.get("paper_id")
        if pid and title:
            return "paper", f"pid:{pid}", rec
        return None

    def annotate(self, result: Any, turn: int = 0) -> Any:
        """A copy of a tool result with an `sid` on every source in it."""
        def walk(x):
            if isinstance(x, dict):
                out = {k: walk(v) for k, v in x.items()}
                if _is_artifact(x):
                    aid = self.artifact_id(x, turn)
                    return {"cite_as": f"[{aid}]", **out} if aid else out
                src = self.source_of(x)
                if src:
                    sid = self.add(*src, turn=turn)
                    out = {"sid": sid, **out}
                return out
            if isinstance(x, list):
                return [walk(v) for v in x]
            return x
        try:
            return walk(copy.deepcopy(result))
        except Exception:     # never cost the model its tool result
            logger.exception("source ledger: annotate failed")
            return result

    def rule_note(self, before: int) -> Optional[str]:
        """The citing rule for a tool result that brought this turn's first
        ids (`before`: the entry count before annotating it); None once the
        rule is in front of the model."""
        if self.rule_shown or len(self.entries) <= before:
            return None
        self.rule_shown = True
        new = list(self.entries.values())[before:]
        rule = CITE_RULE if any(e.kind != "artifact" for e in new) else ARTIFACT_RULE
        return "[citing] " + rule

    def add_user_dois(self, text: str, turn: int = 0) -> None:
        """DOIs the user typed are sources the model may cite."""
        for m in _DOI_RE.finditer(text or ""):
            d = norm_doi(m.group(0))
            self.add("paper", "doi:" + d, {"doi": d, "source_type": "user"}, turn)

    def artifact_id(self, art: dict, turn: int = 0) -> Optional[str]:
        aid = art.get("registered_artifact_id") or art.get("id")
        if not aid:
            return None
        rec = {"title": art.get("filename") or art.get("title") or "artifact",
               "url": art.get("external_url") or "", "artifact_id": aid,
               "content_type": art.get("content_type") or ""}
        return self.add("artifact", f"art:{aid}", {k: v for k, v in rec.items() if v}, turn)

    # ------------------------------------------------------------------
    # The block later turns see
    # ------------------------------------------------------------------
    def block(self, max_chars: int = BLOCK_MAX_CHARS) -> Optional[str]:
        """The system-prompt block, or None while the conversation has no
        sources: a turn that never cites (small talk, code, files) carries no
        citing rule, and a tool result that brings the first sources carries
        it instead (rule_note). Measured 2026-11-25: the rule on every turn
        went with more clarifying questions on the mechanical cases."""
        papers = [e for e in self.entries.values() if e.kind != "artifact"]
        if not papers:
            if any(e.kind == "artifact" for e in self.entries.values()):
                self.rule_shown = True
                return ("=== SOURCES IN THIS CONVERSATION ===\n" + ARTIFACT_RULE
                        + "\n=== END SOURCES ===")
            return None
        papers.sort(key=lambda e: (e.first_turn, int(e.sid[1:])), reverse=True)
        self.rule_shown = True
        head = ("=== SOURCES IN THIS CONVERSATION ===\n" + CITE_RULE + "\n"
                "Sources found earlier in this conversation (citable without a new search):")
        lines, used, left = [], len(head), 0
        for i, e in enumerate(papers):
            line = f"{e.sid}: {_short(e.record)}"
            if used + len(line) + 1 > max_chars:
                left = len(papers) - i
                break
            lines.append(line)
            used += len(line) + 1
        if left:
            lines.append(f"({left} older sources not shown; still citable by id)")
        return head + "\n" + "\n".join(lines) + "\n=== END SOURCES ==="

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    async def enrich(self, sids: Iterable[str]) -> None:
        """Fill cited papers' records from Crossref (citecheck's cached lookup).
        A DOI whose registry title does not match the tool's record is a
        mis-keyed corpus entry: keep the tool's details, drop the DOI."""
        import httpx
        import citecheck
        todo = [self.entries[s] for s in sids
                if s in self.entries and self.entries[s].record.get("doi")
                and not self.entries[s].record.get("_checked")]
        if not todo:
            return
        try:
            async with httpx.AsyncClient(headers={"User-Agent": citecheck._user_agent()},
                                         follow_redirects=True) as client:
                import asyncio
                sem = asyncio.Semaphore(3)

                async def one(e: Entry):
                    async with sem:
                        status, cr = await citecheck.lookup(e.record["doi"], client)
                    e.record["_checked"] = status
                    e.dirty = True
                    if status == "missing":
                        e.record["_doi_bad"] = "not registered"
                    elif status == "ok" and cr:
                        mine = e.record.get("title") or ""
                        if mine and cr.get("title") and citecheck._similar(mine, cr["title"]) < 0.34:
                            e.record["_doi_bad"] = "registered to a different paper"
                        else:
                            for k in ("title", "year", "journal", "volume", "page"):
                                if cr.get(k):
                                    e.record[k] = cr[k]
                            if cr.get("authors"):
                                e.record["authors"] = cr["authors"]
                await asyncio.wait_for(asyncio.gather(*(one(e) for e in todo)),
                                       citecheck.OVERALL_TIMEOUT_S)
        except Exception as exc:   # unchecked records still render from the tool data
            logger.info("source ledger: enrich incomplete: %s", exc)

    def render(self, content: str) -> tuple[str, dict]:
        """Replace [S#]/[A#] with links, append References. Pure."""
        notes = {"cited": 0, "unknown_ids": 0, "artifacts": 0}
        if content and "\x02" in content:
            content = _PLACE_RE.sub(lambda m: _reference(self._best_of(m.group(1))), content)
        if not content or not _CITE_RE.search(content):
            return content, notes
        fences = [(m.start(), m.end()) for m in re.finditer(r"(```|~~~)[\s\S]*?(\1|$)", content)]
        numbers: dict[str, int] = {}
        by_title: dict[str, int] = {}

        def number_of(sid: str) -> int:
            """One number per work: the corpus can hold the same paper twice
            (once under a mangled DOI), and both may be cited."""
            if sid in numbers:
                return numbers[sid]
            t = _title_key(self.entries[sid].record)
            n = by_title.get(t) if t else None
            if n is None:
                n = len(set(numbers.values())) + 1
                if t:
                    by_title[t] = n
            numbers[sid] = n
            return n

        def repl(m: re.Match) -> str:
            if any(a <= m.start() < b for a, b in fences):
                return m.group(0)
            parts = []
            for sid in _ID_RE.findall(m.group(1)):
                e = self.entries.get(sid)
                if not e:
                    notes["unknown_ids"] += 1
                    continue
                if e.kind == "artifact":
                    notes["artifacts"] += 1
                    t = e.record.get("title") or sid
                    url = e.record.get("url")
                    parts.append(f"[{t}]({url})" if url else f"**{t}**")
                    continue
                n = number_of(sid)
                if any(p.startswith(f"[[{n}]]") or p == f"[{n}]" for p in parts):
                    continue
                link = _link(self._best(n, numbers))
                parts.append(f"[[{n}]]({link})" if link else f"[{n}]")
            return ", ".join(parts) if parts else "\x00"

        # Number every cited work first, so a citation rendered before its
        # twin (same work, better DOI) still gets the good link.
        for m in _CITE_RE.finditer(content):
            if not any(a <= m.start() < b for a, b in fences):
                for sid in _ID_RE.findall(m.group(1)):
                    if sid in self.entries and self.entries[sid].kind != "artifact":
                        number_of(sid)
        out = _CITE_RE.sub(repl, content)
        # An id the ledger does not know is removed with the space before it.
        out = re.sub(r"[ \t]*\x00", "", out)
        notes["cited"] = len(set(numbers.values()))
        if numbers:
            refs = [f"{n}. {_reference(self._best(n, numbers))}"
                    for n in sorted(set(numbers.values()))]
            out = out.rstrip() + "\n\n**References**\n\n" + "\n".join(refs) + "\n"
        return out, notes

    def _best_of(self, sid: str) -> dict:
        """The record of sid's work with a good DOI (its twin under a mangled DOI may be cited)."""
        rec = self.entries[sid].record
        t = _title_key(rec)
        if rec.get("doi") and not rec.get("_doi_bad") or not t:
            return rec
        twins = [e.record for e in self.entries.values() if e.kind != "artifact"
                 and _title_key(e.record) == t and e.record.get("doi") and not e.record.get("_doi_bad")]
        return (twins or [rec])[0]

    def _best(self, n: int, numbers: dict) -> dict:
        """Of the entries sharing number n, the record with a good DOI."""
        recs = [self.entries[s].record for s, k in numbers.items() if k == n]
        good = [r for r in recs if r.get("doi") and not r.get("_doi_bad")]
        return (good or recs)[0]

    def cited_ids(self, content: str) -> list[str]:
        ids = [s for m in _CITE_RE.finditer(content or "") for s in _ID_RE.findall(m.group(1))]
        ids += _PLACE_RE.findall(content or "")
        return [s for s in ids if s in self.entries]

    # ------------------------------------------------------------------
    # Typed references (the model still writes some itself)
    # ------------------------------------------------------------------
    def _sid_of_doi(self, doi: str) -> Optional[str]:
        d = norm_doi(unquote(doi)).rstrip("*`")
        sid = self.by_key.get(f"doi:{d}")
        return sid if sid and self.entries[sid].kind != "artifact" else None

    def reconcile(self, content: str) -> tuple[str, dict]:
        """Run before enrich and render. The model cites by id but still types
        references and DOIs (the 2026-11-25 A/B: about 950 id citations and
        400 to 500 typed DOIs per arm, the typed ones carrying the wrong pages,
        years and corpus DOIs). A line that is a reference to a source in the
        ledger (it starts like a reference and carries that source's title) is
        replaced by a placeholder that render fills with the checked
        reference; a typed DOI of a ledger source anywhere else (a table cell,
        prose) becomes that source's id. DOIs the ledger does not know are left
        for citecheck."""
        notes = {"refs_rewritten": 0, "dois_to_ids": 0}
        if not content or not self.entries or not _DOI_RE.search(content):
            return content, notes
        out, fence = [], False
        for line in content.split("\n"):
            if re.match(r"\s*(```|~~~)", line):
                fence = not fence
            # A markdown link definition ("[4]: https://doi.org/...") is left alone.
            if (fence or not _DOI_RE.search(line) and not _CITE_RE.search(line)
                    or re.match(r"\s*\[[^\]]+\]:\s*<?https?://\S+>?\s*$", line)):
                out.append(line)
                continue
            new = self._reference_line(line)
            if new is not None:
                notes["refs_rewritten"] += 1
                out.append(new)
                continue
            new, n = self._dois_to_ids(line)
            notes["dois_to_ids"] += n
            out.append(new)
        return "\n".join(out), notes

    def _dois_to_ids(self, line: str) -> tuple[str, int]:
        n = 0

        def link(m: re.Match) -> str:
            nonlocal n
            sid = self._sid_of_doi(m.group(2))
            if not sid:
                return m.group(0)
            n += 1
            label = m.group(1).strip().strip("*`").strip()
            if not label or _DOI_RE.search(label) or label.lower() in _LINK_WORDS:
                return f"[{sid}]"
            return f"{m.group(1)} [{sid}]"

        def bare(m: re.Match) -> str:
            nonlocal n
            raw = m.group(1)
            doi = raw.rstrip(".,;:)")
            sid = self._sid_of_doi(doi)
            if not sid:
                return m.group(0)
            n += 1
            return f"[{sid}]" + raw[len(doi):]

        line = _DOI_LINK_RE.sub(link, line)
        line = _DOI_BARE_RE.sub(bare, line)
        # "DOI: [S3]" and "**DOI: [S3]**" read as "[S3]"; one id per spot.
        line = re.sub(r"\*{0,2}\b(?:DOI|doi)\b\s*:?\s*\*{0,2}\s*(\[S\d+\])\*{0,2}", r"\1", line)
        line = re.sub(r"(\[(S\d+)\])(?:\s*[,;]?\s*\[\2\])+", r"\1", line)
        line = re.sub(r"\[\s*\[(S\d+)\]\s*\]", r"[\1]", line)   # "[DOI: x]" was bracketed
        return line, n

    def _reference_line(self, line: str) -> Optional[str]:
        """The placeholder line if `line` is a reference to a ledger source."""
        if (line.lstrip().startswith("|") or len(line) > 700 or not _YEAR_RE.search(line)
                or len({norm_doi(unquote(d)).rstrip("*`") for d in _DOI_RE.findall(line)}) > 1):   # several references on one line
            return None
        m = _REF_PREFIX_RE.match(line)
        prefix, rest = line[:m.end()], line[m.end():]
        rest_plain = re.sub(r"^\s*\[S\d+\]\s*", "", rest)
        if not _starts_like_reference(rest_plain):
            return None
        cands = [s for s in (self._sid_of_doi(d) for d in _DOI_RE.findall(line)) if s]
        cands += [s for s in _ID_RE.findall(" ".join(x.group(1) for x in _CITE_RE.finditer(line)))
                  if s in self.entries and self.entries[s].kind != "artifact"]
        if not cands and not _DOI_RE.search(line):
            return None
        import citecheck

        def best(sids, min_tokens):
            scored = [(citecheck._similar(t, line), s) for s in dict.fromkeys(sids)
                      for t in [self.entries[s].record.get("title") or ""]
                      if len(citecheck._tokens(t)) >= min_tokens]
            return max(scored) if scored else (0.0, None)

        sim, sid = best(cands, 4)
        if sim < 0.75:
            # The id or DOI next to the reference may belong to another paper,
            # or the DOI is mangled: any ledger source whose (longer) title the
            # line carries.
            sim, sid = best([s for s, e in self.entries.items()
                             if e.kind != "artifact" and e.record.get("doi")], 5)
            if sim < 0.85:
                return None
        # Commentary after the reference ("- ... DOI ... : why it matters") stays.
        ends = [x.end() for x in (*_DOI_LINK_RE.finditer(rest), *_DOI_BARE_RE.finditer(rest),
                                  *_CITE_RE.finditer(rest))]
        tail = rest[max(ends):] if ends else ""
        tail = re.sub(r"^[\s*.,;:)\]—–-]+", "", tail).strip()
        keep = f" — {tail}" if len(re.findall(r"[A-Za-z]{2,}", tail)) >= 3 else ""
        prefix = re.sub(r"\s*\[S\d+\]\s*$", " ", prefix)
        return f"{prefix}\x02{sid}\x02{keep}"

    # ------------------------------------------------------------------
    # Persistence (chats.db, table conversation_sources)
    # ------------------------------------------------------------------
    @classmethod
    async def load(cls, conversation_id: Optional[str]) -> "Ledger":
        led = cls(conversation_id=conversation_id)
        if not conversation_id:
            return led
        import chat_store
        db = await chat_store.get_db()
        cur = await db.execute(
            "SELECT sid, kind, key, record, first_turn FROM conversation_sources "
            "WHERE conversation_id = ?", (conversation_id,))
        for sid, kind, key, rec, turn in await cur.fetchall():
            try:
                record = json.loads(rec)
            except ValueError:
                record = {}
            led.entries[sid] = Entry(sid, kind, key, record, turn)
            led.by_key[key] = sid
        return led

    async def save(self) -> None:
        dirty = [e for e in self.entries.values() if e.dirty]
        if not self.conversation_id or not dirty:
            return
        import chat_store
        async with chat_store.write_txn() as db:
            await db.executemany(
                "INSERT INTO conversation_sources (conversation_id, sid, kind, key, record, first_turn) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(conversation_id, sid) DO UPDATE SET "
                "record = excluded.record",
                [(self.conversation_id, e.sid, e.kind, e.key,
                  json.dumps(e.record, ensure_ascii=False, default=str), e.first_turn) for e in dirty])
        for e in dirty:
            e.dirty = False


def _is_artifact(d: dict) -> bool:
    if d.get("registered_artifact_id"):
        return True
    aid = d.get("id")
    return isinstance(aid, str) and aid.startswith("art_") and bool(d.get("content_type") or d.get("external_url"))


# ----------------------------------------------------------------------
# Formatting helpers (pure)
# ----------------------------------------------------------------------
def _starts_like_reference(text: str) -> bool:
    """Authors first (Vancouver, APA or initials first) or a quoted, italic or
    bold title first."""
    import citecheck
    t = text.lstrip()
    return bool(citecheck._lead_author(t) or _INITIALS_FIRST_RE.match(t)
                or re.match(r"^(?:\*{1,2}|_|\"|“)[A-Z0-9]", t))


def _title_key(rec: dict) -> str:
    t = re.sub(r"[^a-z0-9]+", " ", (rec.get("title") or "").lower()).strip()
    return t if len(t) >= 20 else ""


def _authors(rec: dict, n: int = MAX_AUTHORS_SHOWN) -> str:
    a = [x if isinstance(x, str) else (x.get("name") or "") for x in rec.get("authors") or []]
    a = [x for x in a if x]
    if not a:
        return ""
    return ", ".join(a[:n]) + (" et al." if len(a) > n else "")


def _journal(rec: dict) -> str:
    return rec.get("journal") or rec.get("venue") or ""


def _link(rec: dict) -> str:
    if rec.get("doi") and not rec.get("_doi_bad"):
        return f"https://doi.org/{rec['doi']}"
    return rec.get("url") or ""


def _short(rec: dict) -> str:
    first = _authors(rec, 1).split(",")[0].replace(" et al.", "")
    bits = [first + (" et al." if len(rec.get("authors") or []) > 1 else "") if first else "",
            str(rec.get("year") or ""), rec.get("title") or rec.get("url") or rec.get("doi") or "",
            _journal(rec)]
    return "; ".join(b for b in bits if b)[:300]


def _reference(rec: dict) -> str:
    parts = []
    if _authors(rec):
        parts.append(_authors(rec).rstrip(".") + ".")
    if rec.get("year"):
        parts.append(f"({rec['year']}).")
    if rec.get("title"):
        parts.append(rec["title"].rstrip(".") + ".")
    j = _journal(rec)
    if j:
        tail = "".join([f" {rec['volume']}" if rec.get("volume") else "",
                        f", {rec['page']}" if rec.get("page") else ""])
        parts.append(f"*{j}*{tail}.")
    link = _link(rec)
    if link:
        parts.append(link)
    elif rec.get("doi") and rec.get("_doi_bad"):
        # The bad DOI itself is not printed: a reader would copy it.
        parts.append("(no verified DOI)")
    return " ".join(parts) or (rec.get("url") or "source")
