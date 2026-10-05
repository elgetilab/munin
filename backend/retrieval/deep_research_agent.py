"""
Deep Research agent: open question -> composite document synthesised from many
sources, via a plan-as-data-structure loop over the search + source agents.

This is the long-running, semi-pinned agent from AGENT-IMPLEMENTATION-PLAN.md
(D1-D13). It is NOT a normal in-turn MCP tool (a multi-minute run cannot block a
chat turn) and NOT a SLURM job (D1): it runs in-process against the live vLLM.
This module is the RESEARCH LOOP; the chat-service integration layer (detached
asyncio task, SSE progress + stream_registry resume, the on/off toggle,
markdown-artifact delivery, configurable-N concurrency) and the external-benchmark
eval (D13) are the deferred layer around it.

The loop (semi-pinned per iteration - only "what are the sub-questions" is free):

    decompose question -> plan[{sub_question, status, notes[], evidence_refs[]}]
    for each open sub-question:
        search  ->  screen (batched, recall-tuned)  ->  source(qa) fan-out
        -> structured notes {claim, value?, unit?, quote, ref, sub_question_id}
        -> status = resolved | unresolvable
    synthesise sections from notes (grouped by sub_question_id; name contradictions)
    -> markdown document + citations carrying read_depth

Key decisions realised here: plan-as-data-structure (auditable + resumable, D7),
batched recall-tuned screener that logs every drop (D8), funnel widths as config
(D9), read_depth per citation (D11), structured contradiction-aware notes (D12),
synthesiser drafts from quotes not paraphrase-of-paraphrase. Plan is checkpointed
to JSON for resume (SQLite conversation_plans migration is a follow-up).
"""

from __future__ import annotations

import json
import os
import re
import uuid
from typing import Any, Awaitable, Callable, Optional

import httpx

from agent_trace import AgentTrace
from database import VLLM_MODEL_NAME, VLLM_URL, thinking_off, LLM_HEADERS

# Async progress callback: `await progress(event: str, data: dict)`. The manager
# passes one that appends a render-ready event to the durable job log (so the
# frontend renders the run inline, and a reconnect replays it); None in a
# bare/eval call. Async so events persist IN ORDER (a fire-and-forget task could
# reorder the log). Non-fatal - a bad callback must never kill the run.
ProgressFn = Callable[[str, dict], Awaitable[None]]


async def _emit(progress: Optional[ProgressFn], event: str, **data: Any) -> None:
    if progress is None:
        return
    try:
        await progress(event, data)
    except Exception:
        pass

# Funnel widths are CONFIG, not agent-chosen (D9) - this is what makes cost
# predictable. Defaults are modest; a full run widens SCREEN_KEEP/READ_CAP.
DEFAULT_MAX_SUBQ = 6         # more sub-questions -> broader coverage
DEFAULT_SCREEN_KEEP = 20     # candidates kept per sub-question after screening
DEFAULT_READ_CAP = 8         # full reads per sub-question (breadth lever 3)
DEFAULT_SNOWBALL_READS = 4   # extra reads per snowball pass
DEFAULT_SNOWBALL_DEPTH = 2   # snowball the snowballed papers once more (depth-2)
_MIN_NOTES_FOR_RECS = 3      # R5: below this, skip Recommendations (too thin)
CHECKPOINT_DIR = os.getenv("DEEP_RESEARCH_DIR", "/data/deep_research")


async def _llm(system: str, user: str, *, max_tokens: int = 1500,
               thinking: bool = False) -> str:
    body = {"model": VLLM_MODEL_NAME,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "max_tokens": max_tokens, "temperature": 0.4, "stream": False}
    if not thinking:
        thinking_off(body)
    async with httpx.AsyncClient(timeout=180.0) as client:
        r = await client.post(f"{VLLM_URL}/v1/chat/completions", json=body,
                              headers=LLM_HEADERS)
    if r.status_code != 200:
        raise RuntimeError(f"vLLM {r.status_code}: {r.text[:150]}")
    return (r.json()["choices"][0]["message"].get("content") or "").strip()


def _parse_json(text: str, default):
    m = re.search(r"[\[{].*[\]}]", text, re.DOTALL)
    if not m:
        return default
    try:
        return json.loads(m.group(0))
    except Exception:
        return default


def _coerce_str_list(parsed) -> list[str]:
    """A list of non-empty strings from an LLM's JSON. Handles the common failure
    where the model wraps the list in an object, e.g. {"sub_questions": [...]} -
    iterating that dict would otherwise yield its KEYS ("sub_questions") as if
    they were the items (the capstone bug)."""
    if isinstance(parsed, dict):
        # Take the first list value; else the string values.
        for v in parsed.values():
            if isinstance(v, list):
                parsed = v
                break
        else:
            parsed = [v for v in parsed.values() if isinstance(v, str)]
    if not isinstance(parsed, list):
        return []
    return [str(s).strip() for s in parsed if isinstance(s, str) and s.strip()]


async def _decompose(question: str, max_subq: int) -> list[str]:
    out = await _llm(
        "You are a research planner. Break the user's question into 2-5 concrete, "
        "independently-answerable sub-questions. Return ONLY a JSON array of "
        "strings (not an object), e.g. [\"...\", \"...\"].",
        f"Question: {question}", max_tokens=500)
    subs = _coerce_str_list(_parse_json(out, []))
    return subs[:max_subq] or [question]


async def _screen(sub_question: str, candidates: list[dict], keep: int,
                  tr: AgentTrace) -> list[dict]:
    """Batched, recall-tuned triage over candidate titles+snippets. A false
    negative (dropping the right paper) is silent and unrecoverable, so err
    permissive and LOG every drop (D8)."""
    if len(candidates) <= keep:
        return candidates
    listing = "\n".join(
        f"[{i}] {c.get('title','')} :: {(c.get('snippet') or '')[:160]}"
        for i, c in enumerate(candidates))
    out = await _llm(
        "You screen candidate papers for relevance to a research sub-question. "
        "ERR ON THE SIDE OF KEEPING - a wrongly dropped paper is unrecoverable. "
        "Return ONLY a JSON list of the indices to KEEP.",
        f"Sub-question: {sub_question}\n\nCandidates:\n{listing}", max_tokens=300)
    idxs = _parse_json(out, list(range(len(candidates))))
    keep_set = {i for i in idxs if isinstance(i, int) and 0 <= i < len(candidates)}
    if not keep_set:
        keep_set = set(range(len(candidates)))
    kept = [c for i, c in enumerate(candidates) if i in keep_set][:keep]
    dropped = [candidates[i].get("title") for i in range(len(candidates))
               if i not in keep_set]
    tr.decide("screen", sub_question=sub_question, kept=len(kept),
              dropped=len(dropped), dropped_titles=dropped[:20])
    return kept


def _answer_line(answer: str) -> str:
    m = re.search(r"answer:\s*(.+)", answer, re.IGNORECASE)
    return (m.group(1).strip() if m else answer.strip())[:400]


# R4: pull a quantity (number + recognised scientific unit) out of a finding so
# the report can carry hard numbers and the synthesiser can contrast them. Only
# number+unit pairs are extracted - a bare count ("5 papers") is not a quantity.
# Longest / most specific units FIRST - regex alternation is first-match, not
# longest-match, so µmol/L must precede µM (which is a substring of "µmol").
_UNIT = (r"(µmol/L|mmol/L|nmol/L|kcal/mol|kJ/mol|M⁻¹|M-1|kDa|Da|"
         r"nM|pM|µM|μM|mM|Å²|Å|%|×|[-\s]?fold|nm|mV|°C|bp|kb)")
_VALUE_RE = re.compile(r"[~≈]?\s*([-+]?\d[\d,]*(?:\.\d+)?)\s*" + _UNIT, re.IGNORECASE)


def _extract_value(text: str) -> tuple[Optional[str], Optional[str]]:
    m = _VALUE_RE.search(text or "")
    if not m:
        return None, None
    val = m.group(1).replace(",", "").replace(" ", "")
    unit = (m.group(2) or "").strip().lstrip("-").strip() or None
    return val, unit


def _numeric_conflicts(notes: list[dict]) -> list[tuple[dict, dict]]:
    """Pairs of notes reporting the SAME unit but a DIFFERENT value - a concrete,
    deterministic contradiction signal (R3/R4). The synthesiser is told to name
    these explicitly rather than average them."""
    out = []
    withval = [n for n in notes if n.get("value") and n.get("unit")]
    for i in range(len(withval)):
        for j in range(i + 1, len(withval)):
            a, b = withval[i], withval[j]
            if a["unit"] == b["unit"] and a["value"] != b["value"]:
                out.append((a, b))
    return out


def _seen_keys(node: dict) -> set:
    """DOIs and URLs already read for this sub-question (dedup across the funnel
    + snowball, and across the corpus/OA/web tiers)."""
    out = set()
    for r in node["evidence_refs"]:
        ref = r.get("ref") or {}
        for k in (ref.get("doi"), ref.get("url")):
            if k:
                out.add(k.lower())
    return out


def _origin_tier(origin: Optional[str]) -> str:
    """Collapse source.origin into the display tier (corpus | oa | web) so the
    timeline can badge each citation by where it came from - the breadth signal."""
    if origin == "web":
        return "web"
    if origin in ("oa_cache", "oa_download", "s2_abstract"):
        return "oa"
    if origin == "local_kb":
        return "corpus"
    return "unknown"


async def _read_candidates(node: dict, candidates: list[dict], cap: int,
                           tr: AgentTrace, progress: Optional[ProgressFn]) -> int:
    """Read up to `cap` candidates with source(findings) - papers by DOI AND web
    pages by URL (lever 1) - and turn each grounded finding into its own note
    (lever 2: multi-note, still quote-backed). Dedups across DOIs+URLs. Returns
    the number of documents read."""
    from mcp.tools.source import source
    sq = node["sub_question"]
    seen = _seen_keys(node)
    reads = 0
    for c in candidates:
        if reads >= cap:
            break
        doi, url = c.get("doi"), c.get("url")
        key = (doi or url or "").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        reads += 1
        ref = {"doi": doi} if doi else {"url": url}
        title = (c.get("title") or doi or url or "source")[:90]
        rc = "tc-" + uuid.uuid4().hex[:8]
        await _emit(progress, "tool_call", id=rc, name="source",
                    arguments={"doi": doi, "url": url, "question": sq, "title": title},
                    sub_question_id=node["id"])
        env = await source(refs=[ref], mode="findings", question=sq)
        outcome, depth_read = env.get("outcome"), env.get("read_depth")
        findings = env.get("findings") or []
        tier = _origin_tier((env.get("source") or {}).get("origin"))
        await _emit(progress, "tool_result", id=rc,
                    summary=f"{title} - {outcome} ({len(findings)} findings, {depth_read}, {tier})",
                    outcome=outcome, read_depth=depth_read, tier=tier,
                    ref=env.get("ref_resolved"))
        node["evidence_refs"].append({"ref": env.get("ref_resolved"),
                                      "read_depth": depth_read, "outcome": outcome,
                                      "tier": tier})
        if outcome == "resolved" and findings:
            for f in findings:
                claim = f["claim"]
                val, unit = _extract_value(claim)
                if not val:
                    val, unit = _extract_value(f.get("quote") or "")
                note = {"claim": claim, "quote": f.get("quote"),
                        "ref": env.get("ref_resolved"), "read_depth": depth_read,
                        "tier": tier, "sub_question_id": node["id"],
                        "value": val, "unit": unit}
                node["notes"].append(note)
                await _emit(progress, "note", sub_question_id=node["id"],
                            claim=claim, quote=f.get("quote"), ref=note["ref"], tier=tier)
    return reads


def _read_order_key(c: dict):
    """Sort key putting the most relevant candidate first, regardless of tier.

    Candidates with no `relevance` (encoder unavailable, so `search` fell back
    to the legacy per-tier sort) go last as a group. Python's sort is stable, so
    within an equal key the original tier-trust order is preserved - which makes
    a missing encoder degrade to exactly the old behaviour.
    """
    rel = c.get("relevance")
    if rel is None:
        return (1, 0.0)
    return (0, -float(rel))


async def _resolve(node: dict, depth: str, read_cap: int, screen_keep: int,
                   tr: AgentTrace, progress: Optional[ProgressFn] = None) -> None:
    """Fill a plan node's notes via search -> screen -> read. Emits tool cards."""
    from mcp.tools.search_agent import search

    sq = node["sub_question"]
    tc = "tc-" + uuid.uuid4().hex[:8]
    await _emit(progress, "tool_call", id=tc, name="search",
                arguments={"query": sq}, sub_question_id=node["id"])
    res = await search(query=sq, depth=depth, top_k=screen_keep + 6)
    # Keep readable candidates: papers (DOI) AND web pages (URL) - lever 1.
    candidates = [c for c in (res or {}).get("ranked", []) if c.get("doi") or c.get("url")]
    # Read in RELEVANCE order, not tier order. `search` returns corpus first for
    # trust, with the reserved OA/web slots appended at the tail; both the
    # screener's `[:keep]` and the read loop's `read_cap` then take from the
    # HEAD, so the external tiers were reserved into `ranked` and immediately
    # truncated back out (measured 2026-07-24: 16 corpus / 2 OA / 0 web reads
    # while the web tier held the highest-relevance candidates). Tier trust
    # still governs how a citation is weighted; it should not decide what is
    # worth reading. Every read is quote-grounded regardless of tier.
    candidates = sorted(candidates, key=_read_order_key)
    kept = await _screen(sq, candidates, screen_keep, tr)
    await _emit(progress, "tool_result", id=tc,
                summary=f"{len(candidates)} candidates, {len(kept)} kept to read")

    reads = await _read_candidates(node, kept, read_cap, tr, progress)
    node["status"] = "resolved" if node["notes"] else "unresolvable"
    tr.decide("resolved sub-question", sub_question=sq, status=node["status"],
              n_notes=len(node["notes"]), n_reads=reads)


async def _snowball(node: dict, source_notes: list[dict], extra_reads: int,
                    screen_keep: int, tr: AgentTrace,
                    progress: Optional[ProgressFn]) -> int:
    """Expansion (R2): pull the references of the given papers, screen them for
    relevance, and read a few more. Called once per depth level. Returns the
    number of NEW notes added (so the caller can drive depth-2 from them)."""
    if not source_notes:
        return 0
    from mcp.tools.s2_citations import s2_get_references
    sq = node["sub_question"]
    seen = _seen_keys(node)
    refs: list[dict] = []
    for n in source_notes[:3]:  # references of the strongest cited papers
        doi = (n.get("ref") or {}).get("doi")
        if not doi:
            continue
        try:
            r = await s2_get_references(doi, limit=40)
        except Exception:  # noqa: BLE001
            continue
        for p in (r.get("references") or []):
            d = p.get("doi")
            if d and d.lower() not in seen:
                seen.add(d.lower())
                refs.append({"doi": d, "title": p.get("title"),
                             "snippet": p.get("abstract") or p.get("tldr") or ""})
    if not refs:
        return 0
    kept = await _screen(sq, refs, screen_keep, tr)
    tr.decide("snowball", sub_question=sq, n_refs=len(refs), n_kept=len(kept))
    before = len(node["notes"])
    await _read_candidates(node, kept, extra_reads, tr, progress)
    if len(node["notes"]) > before and node["status"] != "resolved":
        node["status"] = "resolved"
    return len(node["notes"]) - before


def _group_by_subquestion(notes: list[dict]) -> dict:
    """Group notes by their sub_question_id, preserving order. The unit of
    contradiction detection: conflicting claims live within one sub-question."""
    groups: dict[str, list[dict]] = {}
    for n in notes:
        groups.setdefault(n.get("sub_question_id") or "", []).append(n)
    return groups


async def _synthesise_section(node: dict) -> str:
    notes = node["notes"]
    if not notes:
        return f"## {node['sub_question']}\n\n_No supporting evidence was found._\n"
    # Number the notes so the synthesiser can contrast specific sources.
    def _fmt(i: int, n: dict) -> str:
        val = f"\n    value: {n['value']} {n.get('unit') or ''}".rstrip() if n.get("value") else ""
        return (f"[{i}] claim: {n['claim']}{val}\n"
                f"    quote: \"{(n.get('quote') or '')[:300]}\"\n"
                f"    source: {(n['ref'] or {}).get('title') or (n['ref'] or {}).get('doi')}")
    body = "\n".join(_fmt(i, n) for i, n in enumerate(notes, 1))
    conflicts = _numeric_conflicts(notes)
    if conflicts:
        body += "\n\nCONFLICTING VALUES (name these disagreements explicitly): " + \
            "; ".join(f"{a['value']} {a['unit']} vs {b['value']} {b['unit']}"
                      for a, b in conflicts)
    section = await _llm(
        "You draft one section of a research report from numbered evidence notes. "
        "SYNTHESISE across the notes - connect and contrast them, do not just list. "
        "Draft ONLY from the quoted evidence; add no claims not in the notes; cite "
        "sources by short title. If two notes DISAGREE (opposite claims, or "
        "different values/units for the same quantity), name the disagreement "
        "explicitly and cite both sides rather than averaging or picking one. "
        "3-6 sentences.",
        f"Sub-question: {node['sub_question']}\n\nNotes:\n{body}", max_tokens=700)
    return f"## {node['sub_question']}\n\n{section}\n"


def _cite(ref: Optional[dict]) -> str:
    ref = ref or {}
    return ref.get("title") or ref.get("doi") or "source"


async def _tldr(question: str, notes: list[dict]) -> str:
    """A 3-5 bullet TL;DR of the strongest findings, grounded in the notes."""
    if not notes:
        return ""
    body = "\n".join(f"- {n['claim']} (source: {_cite(n.get('ref'))})" for n in notes[:24])
    out = await _llm(
        "Write a 3-5 bullet TL;DR for a research report from these grounded "
        "findings. Each bullet is a bold headline claim followed by one sentence, "
        "and names its source. Draft ONLY from the findings; add no new claims. "
        "Use '- ' bullets.",
        f"Question: {question}\n\nFindings:\n{body}", max_tokens=600)
    return f"## TL;DR\n\n{out}\n\n" if out else ""


def _key_findings(notes: list[dict]) -> str:
    """Numbered claim + inline citation per grounded note (deterministic)."""
    if not notes:
        return ""
    lines = [f"{i}. {n['claim']} ({_cite(n.get('ref'))})" for i, n in enumerate(notes, 1)]
    return "## Key Findings\n\n" + "\n".join(lines) + "\n\n"


async def _recommendations(question: str, notes: list[dict]) -> str:
    """R5: a short list of actionable recommendations, each grounded in and
    citing a specific finding, with a decision threshold where the evidence
    supports one. Only emitted when the evidence base is rich enough to justify
    them - thin runs skip the section rather than pad it with hollow advice
    (per DR-VS-CLAUDE-COMPARISON.md R5: gated on a real evidence base)."""
    if len(notes) < _MIN_NOTES_FOR_RECS:
        return ""
    body = "\n".join(
        f"- {n['claim']} (source: {_cite(n.get('ref'))})" for n in notes[:24]
    )
    out = await _llm(
        "Write 3-5 actionable recommendations for a research report, drawn ONLY "
        "from these grounded findings. Each recommendation is a bold imperative "
        "headline followed by one or two sentences that (a) name the specific "
        "finding and source it rests on, and (b) where the evidence supports it, "
        "give a concrete decision threshold or condition (a number, a cutoff, or "
        "an 'if X then Y'). Add no claim that is not in the findings; if the "
        "evidence does not support a threshold, omit it rather than inventing "
        "one. Use '- ' bullets.",
        f"Question: {question}\n\nFindings:\n{body}", max_tokens=800)
    return f"## Recommendations\n\n{out}\n\n" if out else ""


def _caveats(plan: list[dict], citations: list[dict]) -> str:
    """Honest limitations: unanswered sub-questions + abstract-only citations."""
    lines: list[str] = []
    unresolved = [n["sub_question"] for n in plan if n["status"] != "resolved"]
    if unresolved:
        lines.append("- The available sources did not answer:")
        lines += [f"  - {u}" for u in unresolved]
    abstract_only = [c for c in citations if c.get("read_depth") == "abstract"]
    if abstract_only:
        lines.append(f"- {len(abstract_only)} source(s) were read at the abstract "
                     "level only, so claims resting on them are less certain.")
    return "## Caveats\n\n" + "\n".join(lines) + "\n" if lines else ""


def _citations(plan: list[dict]) -> list[dict]:
    seen, out = set(), []
    for node in plan:
        for n in node["notes"]:
            ref = n.get("ref") or {}
            key = (ref.get("doi") or ref.get("title") or "").lower()
            if key and key not in seen:
                seen.add(key)
                out.append({"ref": ref, "read_depth": n.get("read_depth"),
                            "tier": n.get("tier")})
    return out


# Job ids name checkpoint files, so only the shape the manager mints is ever
# turned into a path: a client-supplied resume id like "../../app/x" once wrote
# and read arbitrary .json files as root.
_JOB_ID_RE = re.compile(r"dr_[0-9a-f]{16}")


def _checkpoint_path(job_id: str) -> Optional[str]:
    if not isinstance(job_id, str) or not _JOB_ID_RE.fullmatch(job_id):
        return None
    return os.path.join(CHECKPOINT_DIR, f"{job_id}.json")


def _checkpoint(job_id: str, question: str, plan: list[dict]) -> None:
    path = _checkpoint_path(job_id)
    if path is None:
        return
    try:
        os.makedirs(CHECKPOINT_DIR, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"job_id": job_id, "question": question, "plan": plan}, f)
        os.replace(tmp, path)
    except OSError:
        pass


def _load_checkpoint(job_id: str) -> Optional[list[dict]]:
    path = _checkpoint_path(job_id)
    if path is None:
        return None
    try:
        blob = json.load(open(path))
        return blob.get("plan")
    except Exception:
        return None


async def deep_research(question: str, *, depth: str = "deep",
                        max_subq: int = DEFAULT_MAX_SUBQ,
                        screen_keep: int = DEFAULT_SCREEN_KEEP,
                        read_cap: int = DEFAULT_READ_CAP,
                        snowball_reads: int = DEFAULT_SNOWBALL_READS,
                        snowball_depth: int = DEFAULT_SNOWBALL_DEPTH,
                        job_id: Optional[str] = None,
                        progress: Optional[ProgressFn] = None) -> dict:
    """Run the research loop and return {document, plan, citations, trace}.

    Callable synchronously (tests/eval) or as a detached task (the manager wraps
    this). Checkpoints the plan after each sub-question so an interrupted run
    resumes instead of restarting (D6b): pass the same job_id and it reloads the
    plan and skips already-resolved sub-questions. `progress(event, data)` is
    called at each milestone for the manager's durable progress record."""
    if not question or not str(question).strip():
        return {"error": "deep_research requires a question"}
    job_id = job_id or "dr_" + uuid.uuid4().hex[:16]
    tr = AgentTrace("deep_research", question=question, job_id=job_id)

    # Resume from a checkpoint if one exists for this job_id; else plan afresh.
    plan = _load_checkpoint(job_id)
    # The plan event carries id + text + status per sub-question so the frontend
    # renders it as a live checklist (and updates each item as it resolves).
    def _plan_items() -> list[dict]:
        return [{"id": n["id"], "text": n["sub_question"], "status": n["status"]}
                for n in plan]

    if plan:
        tr.decide("resumed from checkpoint", n_sub_questions=len(plan),
                  open=sum(1 for n in plan if n["status"] == "open"))
        await _emit(progress, "plan", items=_plan_items(), resumed=True)
    else:
        subs = await _decompose(question, max_subq)
        tr.llm(1)
        plan = [{"id": f"sq{i}", "sub_question": s, "status": "open",
                 "notes": [], "evidence_refs": []} for i, s in enumerate(subs)]
        tr.decide("plan", n_sub_questions=len(plan))
        _checkpoint(job_id, question, plan)
        await _emit(progress, "plan", items=_plan_items())

    for node in plan:
        if node["status"] != "open":  # resumed: skip already-done sub-questions
            continue
        await _emit(progress, "plan_update", id=node["id"], status="in_progress",
                    sub_question=node["sub_question"])
        try:
            await _resolve(node, depth, read_cap, screen_keep, tr, progress)
            # Depth-1 snowball from the strongest papers (R2), if it resolved.
            # Snowball from the strongest papers (depth 1), then from what that
            # pass adds (depth 2), each bounded by snowball_reads.
            if snowball_reads and node["status"] == "resolved":
                before = len(node["notes"])
                await _snowball(node, node["notes"][:3], snowball_reads,
                                screen_keep, tr, progress)
                new_notes = node["notes"][before:]
                for _ in range(max(0, snowball_depth - 1)):
                    if not new_notes:
                        break
                    mark = len(node["notes"])
                    await _snowball(node, new_notes[:2], snowball_reads,
                                    screen_keep, tr, progress)
                    new_notes = node["notes"][mark:]
        except Exception as exc:  # noqa: BLE001 - one bad sub-question shouldn't kill the run
            if node["status"] == "open":
                node["status"] = "unresolvable"
            tr.decide("sub-question failed", sub_question=node["sub_question"],
                      err=str(exc)[:160])
        _checkpoint(job_id, question, plan)
        await _emit(progress, "plan_update", id=node["id"], status=node["status"],
                    n_notes=len(node["notes"]))

    await _emit(progress, "synthesising",
                n_resolved=sum(1 for n in plan if n["status"] == "resolved"))
    # Claude-style layout, drawn only from grounded notes:
    #   Title / TL;DR / Key Findings / Details / Sources / Recommendations / Caveats
    all_notes = [n for node in plan for n in node["notes"]]
    n_resolved = sum(1 for n in plan if n["status"] == "resolved")
    citations = _citations(plan)

    tldr_md = await _tldr(question, all_notes)
    key_findings_md = _key_findings(all_notes)
    sections = [await _synthesise_section(node) for node in plan]  # Details
    details_md = "## Details\n\n" + "\n".join(sections) if sections else ""
    # Sources: numbered, DOI-linked, with read depth (full-text vs abstract).
    sources_md = ""
    if citations:
        lines = []
        for i, c in enumerate(citations, 1):
            ref = c.get("ref") or {}
            doi = ref.get("doi")
            title = ref.get("title") or doi or "source"
            link = f"[{title}](https://doi.org/{doi})" if doi else title
            tier = c.get("tier")
            tier_tag = f", {tier}" if tier and tier != "unknown" else ""
            lines.append(f"{i}. {link} _(read: {c.get('read_depth') or 'unknown'}{tier_tag})_")
        sources_md = "\n## Sources\n\n" + "\n".join(lines) + "\n"
    recommendations_md = await _recommendations(question, all_notes)
    caveats_md = _caveats(plan, citations)

    document = (f"# {question}\n\n" + tldr_md + key_findings_md + details_md +
                sources_md + "\n" + recommendations_md + caveats_md +
                f"\n---\n_{n_resolved}/{len(plan)} sub-questions resolved from "
                f"{len(all_notes)} grounded notes; {len(citations)} sources cited._\n")

    env = {"job_id": job_id, "document": document, "plan": plan,
           "citations": citations}
    env["trace"] = tr.finish(outcome="resolved", n_sub_questions=len(plan),
                             n_resolved=n_resolved, n_citations=len(citations))
    return env
