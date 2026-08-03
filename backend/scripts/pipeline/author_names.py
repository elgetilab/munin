"""
Author-name sanitising and candidate selection.

Why this exists: author lists in the corpus come from GROBID's parse of the
PDF, and `_merge_metadata` enriched title / journal / year from Crossref but
never authors. So the stored names carry every artefact of a PDF text layer.
Measured over papers_bge (68,426 records, 2026-08-03): 9.2% of records hold at
least one damaged author string and a further 8.5% hold none at all.

Real examples, with the Crossref record for the same DOI:

    'Freda Mil', '! Ergl~a', 'Walfram Tet~laff~~', 'Mark Blsby'
        -> FD Miller, W Tetzlaff, MA Bisby, JW Fawcett, RJ Milner
    'T Wilson$'          -> Herbert H. Winkler, T. Hastings Wilson  (one lost)
    'He Le Á Ne Dutartre'-> Hélène Dutartre                         (mojibake)
    'R Tait1'                              (affiliation superscript glued on)
    'Medical Center', 'Theodor-Kocher Institute'  (affiliations read as people)

Two jobs live here:

  sanitize_authors()  - repair or drop individual name strings.
  best_author_list()  - choose between candidate lists from different sources.

`best_author_list` deliberately does NOT always prefer Crossref: Crossref
often carries initials ("D C Leitman") where the PDF gave the full name
("Dale Leitman"), and a full name is more useful to a reader and to the
citation audit in chat_service. It scores candidates by how many full given
names they carry, so the fuller list wins whatever its source.
"""

from __future__ import annotations

import re
import unicodedata

__all__ = ["sanitize_author", "sanitize_authors", "best_author_list",
           "is_damaged", "DAMAGE_RULES"]

# Characters that never occur in a name but are common in PDF text layers:
# footnote daggers, affiliation markers, TeX leftovers, box-drawing debris.
_ARTEFACT_CHARS = "$†‡¶§*#~^_|\\/<>{}[]"
_ARTEFACT_RE = re.compile(f"[{re.escape(_ARTEFACT_CHARS)}]")

# Affiliation markers, which sit at either end: "R Tait1", "Pam Taylor1,2",
# "Schütz ‡" (trailing) but also "‡ Hooper", "1 Smith" (leading).
_TRAILING_MARKER_RE = re.compile(r"[\s,]*[\d,\s" + re.escape(_ARTEFACT_CHARS) + r"]+$")
_LEADING_MARKER_RE = re.compile(r"^[\d,\s" + re.escape(_ARTEFACT_CHARS) + r"]+")

# An affiliation that GROBID mistook for a person. The stems are open-ended
# (`Institut\w*` covers Institute / Institutes / Institutet) because a closing
# word boundary would miss the commonest spellings.
_AFFILIATION_RE = re.compile(
    r"\b(Universit\w*|Department\w*|Institut\w*|Laborator\w*|School\w*|"
    r"Hospital\w*|Centre\w*|Center\w*|Faculty\w*|Academy\w*|Clinic\w*|"
    r"Foundation\w*|Ltd|GmbH|Inc)\b", re.I)

# Joining words: a single "author" holding several, or a trailing "et al".
_CONJUNCTION_RE = re.compile(r"\b(and|und|et\s+al)\b", re.I)

_EMAIL_RE = re.compile(r"@")
_DIGIT_RE = re.compile(r"\d")
_HAS_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)

# Ligature / diacritic damage: "Gu ¨nther Schu ¨tz", "He Le Á Ne Dutartre".
# The combining-mark repair below handles the first shape; the second (a name
# exploded into capitalised fragments) is not repairable, only detectable.
_FLOATING_DIACRITIC_RE = re.compile(r"\s+([¨´`^~])\s*")
_EXPLODED_CAPS_RE = re.compile(r"^(?:[A-ZÀ-Þ][a-zà-þ]?\s+){3,}[A-ZÀ-Þ][a-zà-þ]?$")

MAX_NAME_CHARS = 45
MAX_NAME_WORDS = 5

# Reported by the audit tool so a report can say WHY a name was rejected.
DAMAGE_RULES: dict[str, re.Pattern] = {
    "artefact_char": _ARTEFACT_RE,
    "digit": _DIGIT_RE,
    "email": _EMAIL_RE,
    "affiliation": _AFFILIATION_RE,
    "conjunction": _CONJUNCTION_RE,
    "exploded_caps": _EXPLODED_CAPS_RE,
}


def _repair_floating_diacritics(name: str) -> str:
    """"Gu \u00a8nther" -> "G\u00fcnther".

    A PDF text layer often emits the combining mark as a separate glyph
    before the letter it belongs to. Recombining is safe because a space
    followed by a bare diacritic is not otherwise meaningful in a name.
    """
    # spacing diacritic -> the equivalent COMBINING mark (U+030x)
    _MAP = {
        "\u00a8": "\u0308",   # diaeresis
        "\u00b4": "\u0301",   # acute
        "`": "\u0300",         # grave
        "^": "\u0302",         # circumflex
        "~": "\u0303",         # tilde
    }

    def _fix(m):
        return " " + _MAP.get(m.group(1), "")

    marked = _FLOATING_DIACRITIC_RE.sub(_fix, name)
    if marked == name:
        return name

    # The mark belongs to the letter BEFORE the space, not after it:
    # "Gu <diaeresis>nther" is "Gü" + "nther", not "Gu" + "n̈ther".
    out: list[str] = []
    i = 0
    while i < len(marked):
        ch = marked[i]
        nxt = marked[i + 1] if i + 1 < len(marked) else ""
        if ch == " " and nxt and unicodedata.combining(nxt) and out:
            out[-1] = out[-1] + nxt        # attach to the preceding base letter
            i += 2
        else:
            out.append(ch)
            i += 1
    return unicodedata.normalize("NFC", "".join(out))


def sanitize_author(name: str) -> str | None:
    """Return a cleaned name, or None when the string is not a usable name.

    Conservative in both directions: it repairs only damage with an
    unambiguous reading (trailing affiliation markers, floating diacritics,
    doubled whitespace) and rejects anything it cannot vouch for. Rejecting
    is safe because the caller falls back to another source; guessing is not,
    because a wrong author name is the exact failure this whole line of work
    started from.
    """
    if not isinstance(name, str):
        return None
    cleaned = unicodedata.normalize("NFC", name).strip()
    if not cleaned:
        return None

    cleaned = _repair_floating_diacritics(cleaned)
    cleaned = _LEADING_MARKER_RE.sub("", cleaned)
    cleaned = _TRAILING_MARKER_RE.sub("", cleaned).strip(" ,;.")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        return None

    # Structural rejects.
    if _EMAIL_RE.search(cleaned) or _AFFILIATION_RE.search(cleaned):
        return None
    if _CONJUNCTION_RE.search(cleaned):
        return None
    if _ARTEFACT_RE.search(cleaned) or _DIGIT_RE.search(cleaned):
        return None
    if len(cleaned) > MAX_NAME_CHARS or len(cleaned.split()) > MAX_NAME_WORDS:
        return None
    if len(cleaned.replace(" ", "")) < 3:
        return None
    if not _HAS_LETTER_RE.search(cleaned):
        return None
    if _EXPLODED_CAPS_RE.match(cleaned):
        return None

    # Mostly-single-letter token runs are OCR debris, not names: "H D O '",
    # "L A R L S O N". Requires three or more tokens, so genuine initial-led
    # forms ("J. Holopainen", "Xu Li") are untouched, and a real name with one
    # initial in the middle ("Antonio M.T. Martins do Canto") stays under the
    # half-the-tokens bar.
    tokens = cleaned.split()
    if len(tokens) >= 3:
        stubs = sum(1 for t in tokens if len(t.strip(".'’\"")) <= 1)
        if stubs * 2 >= len(tokens):
            return None
    return cleaned


def sanitize_authors(names) -> list[str]:
    """Sanitize a list, dropping unusable entries and duplicates (order kept)."""
    out: list[str] = []
    for n in (names or []):
        if isinstance(n, dict):          # pipeline shape: {"name": "..."}
            n = n.get("name")
        cleaned = sanitize_author(n)
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out


def is_damaged(names) -> tuple[bool, list[str]]:
    """(damaged?, reasons) for an author list, without repairing it.

    Used by the audit tool to classify the corpus. A list is damaged when any
    entry fails to survive sanitisation or is altered by it.
    """
    reasons: list[str] = []
    raw = [n.get("name") if isinstance(n, dict) else n for n in (names or [])]
    for n in raw:
        if not isinstance(n, str) or not n.strip():
            reasons.append("empty_or_non_string")
            continue
        for label, rx in DAMAGE_RULES.items():
            if rx.search(n):
                reasons.append(label)
        if len(n) > MAX_NAME_CHARS:
            reasons.append("too_long")
        if len(n.replace(" ", "")) < 3:
            reasons.append("too_short")
        if sanitize_author(n) != n.strip():
            reasons.append("needs_repair")
    return (bool(reasons), sorted(set(reasons)))


def _fullness_score(names: list[str]) -> tuple:
    """Rank an author list by how informative it is.

    Ordered by: count of full given names (a token of 2+ letters that is not
    an initial), then total names, then total characters. This is what makes
    "Dale Leitman" beat Crossref's "D C Leitman" while still preferring
    Crossref's complete list over a GROBID list that lost half the authors.
    """
    full_given = 0
    for n in names:
        tokens = n.split()
        if len(tokens) < 2:
            continue
        # Everything except the last token is a given name / particle. An
        # ALL-CAPS token does not count: some sources shout the whole name
        # ("MICHAEL J. DAWSON"), and a correctly-cased list is the better
        # record even when the shouty one carries an extra middle initial.
        # Case is never rewritten here, because "MCDONALD".title() would
        # yield "Mcdonald" and inventing a spelling is the failure mode this
        # whole line of work exists to remove.
        if any(len(t.strip(".")) > 1 and t.strip(".").isalpha()
               and not t.strip(".").isupper()
               for t in tokens[:-1]):
            full_given += 1
    return (full_given, len(names), sum(len(n) for n in names))


def _is_more_complete(a: list[str], b: list[str]) -> bool:
    """True when `a` holds materially more authors than `b`.

    A dropped author is a worse defect than an abbreviated given name: the
    OCR-damaged record for 10.1523/jneurosci.09-04-01452.1989 kept two
    plausible-looking but misspelled names ("Freda Mil", "Mark Blsby") where
    the paper has five, so a pure fullness score would have preferred the
    wreckage. Materially means two more names, or half again as many.
    """
    if not b:
        return bool(a)
    return len(a) >= len(b) + 2 or len(a) >= 1.5 * len(b)


def _better(a: list[str], b: list[str]) -> bool:
    """Is candidate `a` a better author list than the incumbent `b`?"""
    if not b:
        return bool(a)
    if _is_more_complete(a, b):
        return True
    if _is_more_complete(b, a):
        return False
    # Comparable length: prefer real given names over initials, then the
    # more detailed rendering.
    sa, sb = _fullness_score(a), _fullness_score(b)
    return sa > sb


def best_author_list(*candidates) -> list[str]:
    """Pick the most informative sanitized author list among the candidates.

    Each candidate is a raw list (strings or {"name": ...} dicts) from one
    source. Sanitisation runs first so a list is judged on what would actually
    be stored. Ties go to the earlier candidate, so callers should pass their
    preferred source first.

    Two competing goods are traded off here, in this order: completeness (do
    not lose an author) beats fullness (spell the given name out).
    """
    best: list[str] = []
    for cand in candidates:
        cleaned = sanitize_authors(cand)
        if cleaned and _better(cleaned, best):
            best = cleaned
    return best
