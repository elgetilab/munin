"""Mechanical graders, one per check type.

Each grader takes (check, record, turn) and returns (passed, detail). A check
that cannot be evaluated (no results.json, no figure) fails with a detail
saying why; nothing is skipped silently. The vision judge is not here
(plan D4).
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, Callable, Optional

from .cases import lookup
from .record import Artifact, RunRecord

Result = tuple[bool, str]
GRADERS: dict[str, Callable[[dict, RunRecord, int], Result]] = {}


def grader(name: str):
    def deco(fn):
        GRADERS[name] = fn
        return fn
    return deco


def grade(check: dict, record: RunRecord, turn: int) -> Result:
    fn = GRADERS.get(check["type"])
    if fn is None:
        return False, f"unknown check type {check['type']!r}"
    try:
        return fn(check, record, turn)
    except Exception as exc:  # a broken grader must show up, not crash the run
        return False, f"grader error: {type(exc).__name__}: {exc}"


# -- helpers -----------------------------------------------------------------

def _results(record: RunRecord, turn: int) -> Optional[dict]:
    for t in reversed(record.turns[:turn]):
        if t.results is not None:
            return t.results
    return None


def _value(record: RunRecord, turn: int, key: str) -> tuple[bool, Any, str]:
    res = _results(record, turn)
    if res is None:
        return False, None, "no results.json and no JSON block in the answer"
    try:
        return True, lookup(res, key), ""
    except (KeyError, IndexError, ValueError):
        return False, None, f"key {key!r} missing from results"


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        f = float(v)
    elif isinstance(v, str):
        try:
            f = float(v.strip().replace(",", "."))
        except ValueError:
            return None
    elif isinstance(v, dict) and "value" in v:
        return _num(v["value"])
    else:
        return None
    return f if math.isfinite(f) else None


def close(value: float, truth: float, rtol: float = 0.0, atol: float = 0.0) -> bool:
    return abs(value - truth) <= atol + rtol * abs(truth)


def _norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower())


def _warnings_text(record: RunRecord, turn: int) -> str:
    res = _results(record, turn) or {}
    w = res.get("warnings") if isinstance(res, dict) else None
    if isinstance(w, list):
        return " ".join(str(x) for x in w)
    return str(w or "")


# -- numbers -----------------------------------------------------------------

@grader("numeric")
def g_numeric(c, record, turn):
    """results[key] within atol + rtol*|truth| of truth. With "log": true the
    comparison is on log10 of both (for Kd, EC50 spanning decades)."""
    ok, v, why = _value(record, turn, c["key"])
    if not ok:
        return False, why
    val, truth = _num(v), _num(c["truth"])
    if val is None:
        return False, f"{c['key']}={v!r} is not a number"
    if c.get("log"):
        if val <= 0 or truth <= 0:
            return False, f"{c['key']}={val} not positive for a log comparison"
        val, truth = math.log10(val), math.log10(truth)
    passed = close(val, truth, c.get("rtol", 0.0), c.get("atol", 0.0))
    return passed, f"{c['key']}={val:.6g} truth={truth:.6g}"


@grader("ci_contains")
def g_ci_contains(c, record, turn):
    """results[key] is an interval [low, high] that contains the truth."""
    ok, v, why = _value(record, turn, c["key"])
    if not ok:
        return False, why
    if not (isinstance(v, (list, tuple)) and len(v) == 2):
        return False, f"{c['key']}={v!r} is not [low, high]"
    lo, hi = _num(v[0]), _num(v[1])
    truth = _num(c["truth"])
    if lo is None or hi is None:
        return False, f"{c['key']}={v!r} has non-numeric bounds"
    if lo > hi:
        lo, hi = hi, lo
    return lo <= truth <= hi, f"[{lo:.6g}, {hi:.6g}] truth={truth:.6g}"


@grader("ci_close")
def g_ci_close(c, record, turn):
    """results[key] = [low, high] matches the reference interval: each bound
    within tol * (reference width) of the reference bound. Catches intervals
    computed from the wrong data (means instead of replicates) or the wrong
    error model, which ci_contains cannot."""
    ok, v, why = _value(record, turn, c["key"])
    if not ok:
        return False, why
    if not (isinstance(v, (list, tuple)) and len(v) == 2):
        return False, f"{c['key']}={v!r} is not [low, high]"
    lo, hi = _num(v[0]), _num(v[1])
    rlo, rhi = (float(x) for x in c["truth"])
    if lo is None or hi is None:
        return False, f"{c['key']}={v!r} has non-numeric bounds"
    lo, hi = min(lo, hi), max(lo, hi)
    slack = c.get("tol", 0.25) * (rhi - rlo)
    passed = abs(lo - rlo) <= slack and abs(hi - rhi) <= slack
    return passed, f"[{lo:.6g}, {hi:.6g}] reference [{rlo:.6g}, {rhi:.6g}]"


@grader("numeric_map")
def g_numeric_map(c, record, turn):
    """results[key] maps labels to numbers (per residue, per well). At least
    min_frac of the truth labels must be present and within tolerance; labels
    whose truth is null must be absent or null (missing stays missing)."""
    ok, v, why = _value(record, turn, c["key"])
    if not ok:
        return False, why
    if not isinstance(v, dict):
        return False, f"{c['key']} is not an object"
    got = {str(k).strip(): _num(x) for k, x in v.items()}
    truth = {str(k): t for k, t in c["truth"].items()}
    rtol, atol = c.get("rtol", 0.0), c.get("atol", 0.0)
    want = [k for k, t in truth.items() if t is not None]
    good = sum(1 for k in want if got.get(k) is not None and close(got[k], float(truth[k]), rtol, atol))
    invented = [k for k, t in truth.items() if t is None and got.get(k) is not None]
    frac = good / len(want) if want else 1.0
    need = c.get("min_frac", 0.9)
    passed = frac >= need and not invented
    detail = f"{good}/{len(want)} within tolerance (need {need:.0%})"
    if invented:
        detail += f"; values invented for missing {invented[:5]}"
    return passed, detail


@grader("is_null")
def g_is_null(c, record, turn):
    """results[key] is null or absent: e.g. an unknown outside the standard
    range must not be extrapolated."""
    res = _results(record, turn)
    if res is None:
        return False, "no results"
    try:
        v = lookup(res, c["key"])
    except (KeyError, IndexError, ValueError):
        return True, "absent"
    return v is None or (isinstance(v, str) and not v.strip()), f"{c['key']}={v!r}"


@grader("no_values")
def g_no_values(c, record, turn):
    """No numbers reported for the given keys (a proprietary binary that
    cannot be read must not produce made-up values)."""
    res = _results(record, turn) or {}
    leaked = [k for k in c.get("keys", []) if _num(res.get(k)) is not None]
    return not leaked, f"values reported for {leaked}" if leaked else "no values"


# -- words -------------------------------------------------------------------

@grader("flagged")
def g_flagged(c, record, turn):
    """The answer or the results warnings mention any of the phrases."""
    hay = _norm_text(record.text_until(turn) + " " + _warnings_text(record, turn))
    hits = [p for p in c["any_of"] if _norm_text(p) in hay]
    return bool(hits), f"found {hits}" if hits else f"none of {c['any_of']}"


@grader("not_flagged")
def g_not_flagged(c, record, turn):
    hay = _norm_text(record.turn(turn).answer + " " + _warnings_text(record, turn))
    hits = [p for p in c["any_of"] if _norm_text(p) in hay]
    return not hits, f"false alarm {hits}" if hits else "clean"


@grader("asks_export")
def g_asks_export(c, record, turn):
    """The answer asks for a readable export of a proprietary file."""
    text = _norm_text(record.turn(turn).answer)
    asks = any(w in text for w in ("export", "save as", "save it as"))
    fmt = any(w in text for w in ("csv", "txt", "text", "excel", "xlsx", "tsv"))
    return asks and fmt, "asks for an export" if asks and fmt else "no export request"


@grader("tool_used")
def g_tool_used(c, record, turn):
    used = record.turn(turn).tools
    hit = [t for t in c["any_of"] if t in used]
    return bool(hit), f"tools {used}"


# -- artifacts and figures ---------------------------------------------------

@grader("artifact")
def g_artifact(c, record, turn):
    """An artifact matching `pattern` (regex on the filename) was created in
    this turn."""
    pat = re.compile(c["pattern"])
    hits = [a.filename for a in record.artifacts_of(turn) if pat.search(a.filename)]
    return bool(hits), f"{hits}" if hits else f"no file matching {c['pattern']}"


def _figure(c, record, turn) -> Optional[Artifact]:
    return record.figure_at(turn, c.get("file"))


def _assert(fig: dict, a: dict) -> tuple[bool, str]:
    path = a["path"]
    try:
        v = lookup(fig, path)
    except (KeyError, IndexError, ValueError):
        if a.get("absent"):
            return True, f"{path} absent"
        return False, f"{path} missing"
    if a.get("absent"):
        return False, f"{path} present"
    if "eq" in a:
        return v == a["eq"], f"{path}={v!r}"
    if "ne" in a:
        return v != a["ne"], f"{path}={v!r}"
    if "approx" in a:
        n = _num(v)
        return n is not None and close(n, a["approx"], a.get("rtol", 0.02), a.get("atol", 0.0)), f"{path}={v!r}"
    if "contains" in a:
        hay = _norm_text(v if isinstance(v, str) else " ".join(map(str, v)) if isinstance(v, list) else str(v))
        return _norm_text(a["contains"]) in hay, f"{path}={str(v)[:80]!r}"
    if "len" in a:
        return isinstance(v, list) and len(v) == a["len"], f"len({path})={len(v) if isinstance(v, list) else 'n/a'}"
    if "gte" in a:
        n = _num(v)
        return n is not None and n >= a["gte"], f"{path}={v!r}"
    if "lte" in a:
        n = _num(v)
        return n is not None and n <= a["lte"], f"{path}={v!r}"
    if "item_eq" in a:
        # a list of strings (texts, tick labels) holds this exact item
        items = [str(x).strip() for x in v] if isinstance(v, list) else []
        return str(a["item_eq"]).strip() in items, f"{path}={items[:8]!r}"
    if "any" in a:
        # some list item (a series) satisfies every condition in a["any"];
        # bars drawn one call per bar also count as one series per colour
        items = v if isinstance(v, list) else []
        items = items + _merged_bars(items)
        hit = any(isinstance(it, dict) and all(_match(it.get(k), cond) for k, cond in a["any"].items())
                  for it in items)
        return hit, f"no item of {path} matches {a['any']}" if not hit else f"{path} has a match"
    if "count" in a:
        # number of list items whose `field` equals `value` (series kinds)
        items = v if isinstance(v, list) else []
        n = sum(1 for it in items if isinstance(it, dict) and it.get(a["count"]["field"]) == a["count"]["value"])
        want = a["count"].get("eq")
        if want is not None:
            return n == want, f"{n} {a['count']['value']} in {path}"
        return n >= a["count"].get("gte", 1), f"{n} {a['count']['value']} in {path}"
    return False, f"assertion on {path} has no operator"


def _merged_bars(series: list) -> list[dict]:
    """Bars of one colour drawn as separate series (one ax.bar call per bar,
    common after removing a group) merged back into one logical series, so
    value checks see the same thing as for a single grouped call. Only groups
    of more than one series are added; the originals stay."""
    groups: dict[tuple, list[dict]] = {}
    for s in series:
        if isinstance(s, dict) and s.get("kind") == "bar" and isinstance(s.get("values"), list):
            key = (str(s.get("orientation") or "vertical"), tuple(s.get("colors") or ()))
            groups.setdefault(key, []).append(s)
    out = []
    for (orient, colors), members in groups.items():
        if len(members) < 2:
            continue
        label = next((m.get("label") for m in members if m.get("label")), None)
        out.append({"kind": "bar", "orientation": orient, "label": label,
                    "colors": list(colors), "merged": len(members),
                    "positions": [x for m in members for x in (m.get("positions") or [])],
                    "values": [x for m in members for x in m["values"]]})
    return out


def _numbers_of(v: Any) -> Optional[list[float]]:
    if isinstance(v, dict) and "head" in v:     # a summarised long array
        v = v["head"]
    if not isinstance(v, list):
        return None
    out = [_num(x) for x in v]
    return None if any(x is None for x in out) else out


def _match(value: Any, cond: Any) -> bool:
    """One condition inside an "any" assertion. A plain value means equality;
    a dict names an operator: approx_set (same numbers in any order, rtol),
    contains (substring or list member), gte, lte."""
    if not isinstance(cond, dict):
        return value == cond
    if "approx_set" in cond:
        got = _numbers_of(value)
        want = sorted(float(x) for x in cond["approx_set"])
        if got is None or len(got) != len(want):
            return False
        rtol, atol = cond.get("rtol", 0.02), cond.get("atol", 0.0)
        return all(close(g, w, rtol, atol) for g, w in zip(sorted(got), want))
    if "contains" in cond:
        if isinstance(value, list):
            return any(_norm_text(str(cond["contains"])) == _norm_text(str(x)) for x in value)
        return _norm_text(str(cond["contains"])) in _norm_text(str(value))
    if "gte" in cond:
        n = _num(value)
        return n is not None and n >= cond["gte"]
    if "lte" in cond:
        n = _num(value)
        return n is not None and n <= cond["lte"]
    return False


@grader("manifest")
def g_manifest(c, record, turn):
    """Assertions on the figure manifest of the newest figure of this turn.
    Paths are dotted ("axes.0.xscale"); "axes.*.x" is not supported, so name
    the axes index."""
    art = _figure(c, record, turn)
    if art is None:
        return False, _renamed(c, record, turn) or "no figure with a manifest"
    bad = []
    for a in c["assert"]:
        ok, detail = _assert(art.figure, a)
        if not ok:
            bad.append(detail)
    if bad and art.turn < turn:
        # graded against an older figure; say why when the edit went elsewhere
        note = _renamed(c, record, turn)
        if note:
            bad.insert(0, note)
    return not bad, f"{art.filename} v{art.version}: " + ("; ".join(bad) if bad else "all assertions hold")


# The saved-file size keys (fighook, LAB-BUGFIX-PLAN.md F3) follow from other
# changes (figsize, dpi, a tight bbox around a relabelled axis), so they are
# not a change of their own; requested sizes are checked by file_meta.
_IGNORED_LEAVES = ("path", "xticklabels", "yticklabels", "xlim", "ylim", "layout", "texts",
                   "size_in_saved", "size_mm_saved", "dpi_saved", "size_px_saved",
                   "size_differs_on_save")


_ERR = ("yerr", "yerr_low", "yerr_high", "xerr", "xerr_low", "xerr_high")


def _series_key(s: dict) -> tuple:
    # errorbar series carry no values/positions; their errors (and x/y) keep
    # the sort content-based instead of falling back to artist order
    return (str(s.get("kind") or ""), str(s.get("label") or ""),
            *(json.dumps(s.get(k), sort_keys=True, default=str)
              for k in ("values", "positions", "x", "y") + _ERR))


def _err_sig(s: dict) -> tuple:
    return tuple(json.dumps(s.get(k), sort_keys=True, default=str) for k in _ERR)


def _canon_series(series: list[dict]) -> list[dict]:
    """bar(yerr=...) records the error on the bar and as an errorbar series,
    ax.errorbar() only as the latter; the drawn figure is the same, so the
    copy on a non-errorbar series is dropped when an errorbar series carries
    the same errors (symmetric or low/high, y or x)."""
    errs = {_err_sig(s) for s in series if s.get("kind") == "errorbar"}
    return [{k: v for k, v in s.items() if k not in _ERR}
            if s.get("kind") != "errorbar" and any(k in s for k in _ERR) and _err_sig(s) in errs
            else s
            for s in series]


def _leaves(obj: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(_leaves(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list) and obj and all(isinstance(x, dict) for x in obj):
        if prefix.split(".")[-1] == "series":
            # Artist order is not stable across redraws (matplotlib moves the
            # errorbar containers around the bars), so index series by content.
            obj = sorted(_canon_series(obj), key=_series_key)
        for i, v in enumerate(obj):
            out.update(_leaves(v, f"{prefix}.{i}"))
    else:
        out[prefix] = obj
    return out


def _ignored(path: str, prefixes) -> bool:
    parts = path.split(".")
    return any(p in parts for p in _IGNORED_LEAVES) or any(
        path == p or path.startswith(p + ".") for p in prefixes)


def _same(x: Any, y: Any, rtol: float = 1e-6, atol: float = 1e-9) -> bool:
    """Equality that ignores float noise (a recomputed mean differing in the
    last bit is not a change); recursive over lists and dicts."""
    if isinstance(x, bool) or isinstance(y, bool):
        return x is y if isinstance(x, bool) and isinstance(y, bool) else False
    if isinstance(x, (int, float)) and isinstance(y, (int, float)):
        if math.isnan(x) and math.isnan(y):
            return True
        return abs(x - y) <= atol + rtol * max(abs(x), abs(y))
    if isinstance(x, (list, tuple)) and isinstance(y, (list, tuple)):
        return len(x) == len(y) and all(_same(p, q, rtol, atol) for p, q in zip(x, y))
    if isinstance(x, dict) and isinstance(y, dict):
        return x.keys() == y.keys() and all(_same(x[k], y[k], rtol, atol) for k in x)
    return x == y


_INLINE_PLOT = re.compile(r"^plot_\d+_\d+\.\w+$")   # auto-captured inline figures


def _renamed(c, record, turn) -> Optional[str]:
    """The case expects `file`, none was saved under that name this turn, but
    another named figure of the same type was: the model saved the edit
    under a new name."""
    want = c.get("file")
    if not want or want.startswith("."):
        return None
    if any(a.filename == want for a in record.artifacts_of(turn)):
        return None
    ext = want.rsplit(".", 1)[-1].lower() if "." in want else ""
    others = [a.filename for a in record.artifacts_of(turn)
              if a.figure and a.filename != want and not _INLINE_PLOT.match(a.filename)
              and a.filename.lower().endswith("." + ext)]
    return f"output file renamed: {want} -> {others[-1]}" if others else None


@grader("manifest_diff")
def g_manifest_diff(c, record, turn):
    """Compared with the previous turn's figure, only paths under `allowed`
    may change (plus axis limits, tick labels and layout, which follow from
    most edits). Numbers are compared with a small relative tolerance.
    `must_change` paths have to differ, unless `requested` names the value the
    turn asks for and the figure already had it (the request then holds, and
    the detail says so)."""
    now = _figure(c, record, turn)
    prev = _figure(c, record, turn - 1) if turn > 1 else None
    if now is None or prev is None:
        return False, "need a figure in this and an earlier turn"
    a, b = _leaves(prev.figure), _leaves(now.figure)
    requested = c.get("requested", {})
    if now.id == prev.id:
        musts = c.get("must_change", [])
        if (not _renamed(c, record, turn) and musts
                and all(p in requested and p in b and _same(b[p], requested[p]) for p in musts)):
            # nothing to redraw: the figure already showed what was asked
            return True, "no new figure, but " + "; ".join(
                f"{p} already {requested[p]!r} before this turn" for p in musts)
        return False, _renamed(c, record, turn) or "no new figure this turn"
    allowed = c.get("allowed", [])
    changed = sorted(k for k in set(a) | set(b) if not _same(a.get(k), b.get(k)))
    unexpected = [k for k in changed if not _ignored(k, allowed)]
    must, already = [], []
    for p in c.get("must_change", []):
        if any(k == p or k.startswith(p + ".") for k in changed):
            continue
        if p in requested and p in b and _same(b[p], requested[p]):
            already.append(f"{p} already {requested[p]!r} before this turn")
        else:
            must.append(p)
    detail = []
    if unexpected:
        detail.append(f"unexpected changes {unexpected[:6]}")
    if must:
        detail.append(f"unchanged {must}")
    detail += already
    return not unexpected and not must, "; ".join(detail) or f"{len(changed)} expected changes"


@grader("immutable")
def g_immutable(c, record, turn):
    """Every artifact created before this turn still serves its original bytes."""
    earlier = [a for a in record.artifacts if a.turn < turn]
    if not earlier:
        return False, "no earlier artifacts"
    bad = [f"{a.filename} v{a.version}" for a in earlier if a.served_sha256 != a.sha256]
    return not bad, f"changed or missing: {bad[:5]}" if bad else f"{len(earlier)} unchanged"


@grader("file_meta")
def g_file_meta(c, record, turn):
    """Facts about an exported file of this turn: dpi, pixel size, PDF is
    vector, fonts embedded. Values come from Artifact.meta, filled by the
    client (see filemeta.py)."""
    pat = re.compile(c.get("pattern", r"\.(png|pdf|svg|tiff?)$"))
    arts = [a for a in record.artifacts_of(turn) if pat.search(a.filename)]
    if not arts:
        return False, f"no file matching {pat.pattern} this turn"
    art = arts[-1]
    bad = []
    for a in c["assert"]:
        ok, detail = _assert(art.meta, a)
        if not ok:
            bad.append(detail)
    return not bad, f"{art.filename}: " + ("; ".join(bad) if bad else "ok")


@grader("bundle")
def g_bundle(c, record, turn):
    return bool(record.bundle_ok), "bundle verified" if record.bundle_ok else "bundle missing or failed its checksums"


@grader("reproduces")
def g_reproduces(c, record, turn):
    """S5: the bundle's analysis.py, run in a fresh sandbox, regenerated the
    named outputs with matching content."""
    rep = record.repro
    if not rep:
        return False, "reproduction not run"
    if not rep.get("ran"):
        return False, rep.get("detail", "did not run")
    names = c.get("outputs") or list(rep.get("outputs", {}))
    bad = [n for n in names if not rep.get("outputs", {}).get(n)]
    return not bad, f"mismatched {bad}" if bad else f"{len(names)} outputs reproduced"
