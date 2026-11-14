"""Offline tests for the lab-analysis harness (no model, no live service).

- every fixture case loads and every check names a grader;
- an oracle answer built from truth.json passes every check of every case,
  so the truth wiring and tolerances are consistent;
- the naive answer to each trap fails, so the traps discriminate;
- graders, file metadata and the reproduction comparison behave on
  hand-made inputs;
- the fixtures regenerate identically (data byte-identical, reference values
  within 1e-4, since those come from scipy fits).
"""

from __future__ import annotations

import copy
import io
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from munin_bench.labanalysis import graders
from munin_bench.labanalysis.cases import RESULTS_SUFFIX, load_cases
from munin_bench.labanalysis.client import outputs_match
from munin_bench.labanalysis.filemeta import file_meta, png_meta
from munin_bench.labanalysis.record import Artifact, RunRecord, TurnRecord, results_from_answer
from munin_bench.labanalysis.run import grade_record

BENCH = Path(__file__).resolve().parent.parent
LAB = BENCH / "fixtures" / "lab"
CASES = load_cases(LAB / "cases")
BY_ID = {c.id: c for c in CASES}


def _record(case, results_per_turn=None, answers=None):
    rec = RunRecord(case_id=case.id, suite=case.suite, rep=1, conversation_id="c")
    for i, t in enumerate(case.turns, start=1):
        rec.turns.append(TurnRecord(index=i, prompt=t.prompt,
                                    answer=(answers or {}).get(i, ""),
                                    results=(results_per_turn or {}).get(i)))
    return rec


def _oracle(case):
    """A perfect answer for the numeric/word checks of a case."""
    per_turn, answers = {}, {}
    for i, t in enumerate(case.turns, start=1):
        res = {"warnings": []}
        for c in t.checks:
            kind = c["type"]
            if kind in ("numeric", "ci_close", "ci_contains", "numeric_map"):
                res[c["key"]] = copy.deepcopy(c["truth"])
            elif kind == "is_null":
                res[c["key"]] = None
            elif kind == "flagged":
                res["warnings"].append(c["any_of"][0])
            elif kind == "asks_export":
                answers[i] = "I cannot read this binary format. Please export it as CSV or Excel (xlsx)."
        per_turn[i] = res
    return per_turn, answers


ORACLE_TYPES = {"numeric", "ci_close", "ci_contains", "numeric_map", "is_null", "flagged",
                "asks_export", "no_values", "not_flagged"}


def test_fixture_set_is_complete():
    suites = {c.suite for c in CASES}
    assert {"S1", "S2", "S3", "S5", "S6"} <= suites
    assert sum(c.id.startswith("s1_nist_") for c in CASES) == 27
    assert len(CASES) >= 90


def test_every_check_has_a_grader():
    for case in CASES:
        for t in case.turns:
            for c in t.checks:
                assert c["type"] in graders.GRADERS, (case.id, c["type"])


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_oracle_answer_passes(case):
    per_turn, answers = _oracle(case)
    rec = _record(case, per_turn, answers)
    for i, t in enumerate(case.turns, start=1):
        for c in t.checks:
            if c["type"] not in ORACLE_TYPES:
                continue
            ok, detail = graders.grade(c, rec, i)
            assert ok, f"{case.id} turn {i} {c['type']} {c.get('key')}: {detail}"


def _check(case_id, key):
    return next(c for t in BY_ID[case_id].turns for c in t.checks if c.get("key") == key)


def test_weighting_trap_rejects_the_unweighted_fit():
    case = BY_ID["s1_trap_weighting"]
    c = _check("s1_trap_weighting", "k")
    rec = _record(case, {1: {"k": case.truth["ref"]["k_unweighted"]}})
    assert not graders.grade(c, rec, 1)[0]
    rec = _record(case, {1: {"k": case.truth["ref"]["k_weighted_by_fit"]}})
    assert graders.grade(c, rec, 1)[0], "1/Yfit^2 weighting must also pass"


def test_outlier_trap_rejects_the_naive_fit():
    case = BY_ID["s1_trap_outlier"]
    c = _check("s1_trap_outlier", "logEC50")
    rec = _record(case, {1: {"logEC50": case.truth["ref"]["logEC50_naive"]}})
    assert not graders.grade(c, rec, 1)[0]


def test_standard_curve_rejects_extrapolation():
    case = BY_ID["s1_standard_s1"]
    nulls = [c for c in case.turns[0].checks if c["type"] == "is_null"]
    assert nulls
    rec = _record(case, {1: {nulls[0]["key"]: 171.3}})
    assert not graders.grade(nulls[0], rec, 1)[0]


def test_titration_rejects_invented_values_for_missing_residues():
    case = BY_ID["s6_hsqc_titration_s1"]
    c = _check("s6_hsqc_titration_s1", "csp")
    filled = {k: (v if v is not None else 0.01) for k, v in c["truth"].items()}
    rec = _record(case, {1: {"csp": filled}})
    ok, detail = graders.grade(c, rec, 1)
    assert not ok and "invented" in detail


def test_binary_trap_rejects_made_up_numbers():
    case = BY_ID["s2_binary_sda"]
    no_vals = next(c for c in case.turns[0].checks if c["type"] == "no_values")
    rec = _record(case, {1: {"mean_od": 0.42}}, {1: "The mean OD is 0.42."})
    assert not graders.grade(no_vals, rec, 1)[0]
    assert not graders.grade({"type": "asks_export"}, rec, 1)[0]


def test_ci_close_tells_wrong_intervals_apart():
    case = BY_ID["s1_4pl_s1"]
    c = _check("s1_4pl_s1", "logEC50_ci")
    lo, hi = c["truth"]
    w = hi - lo
    good = _record(case, {1: {"logEC50_ci": [lo + 0.1 * w, hi - 0.1 * w]}})
    bad = _record(case, {1: {"logEC50_ci": [lo - w, hi + w]}})
    assert graders.grade(c, good, 1)[0] and not graders.grade(c, bad, 1)[0]


def test_numeric_log_and_missing_results():
    c = {"type": "numeric", "key": "kd", "truth": 100.0, "log": True, "atol": 0.05}
    case = BY_ID["s1_4pl_s1"]
    assert graders.grade(c, _record(case, {1: {"kd": 110.0}}), 1)[0]
    assert not graders.grade(c, _record(case, {1: {"kd": 150.0}}), 1)[0]
    ok, detail = graders.grade(c, _record(case, {}), 1)
    assert not ok and "no results" in detail
    assert graders.grade(c, _record(case, {1: {"kd": "1,1e2"}}), 1)[0]   # comma decimal string


def test_results_fallback_from_answer():
    text = 'Done.\n```json\n{"a": 1}\n```\nand finally\n```json\n{"logEC50": -0.5}\n```'
    assert results_from_answer(text) == {"logEC50": -0.5}
    assert results_from_answer("no json here") is None


def test_results_request_goes_on_the_right_turn():
    bars = BY_ID["s3_bars"]
    prompts = bars.prompts()
    assert RESULTS_SUFFIX.split("{keys}")[0][:40] in prompts[5]
    assert '"p_ko1"' in prompts[5] and '"p_ko1"' not in prompts[-1]
    single = BY_ID["s1_mm_s1"].prompts()
    assert '"Km_ci"' in single[0]


# -- figures ------------------------------------------------------------------

def _fig_record(figs):
    """figs: list of (turn, filename, figure dict)."""
    case = BY_ID["s3_bars"]
    rec = _record(case)
    for n, (turn, name, fig) in enumerate(figs):
        rec.artifacts.append(Artifact(id=f"a{n}", filename=name, sha256=f"{n:064x}", version=n + 1,
                                      turn=turn, figure=fig, served_sha256=f"{n:064x}"))
    return rec


def _fig(yscale="linear", yerr=(1.0, 2.0), ylabel="y", texts=()):
    return {"size_mm": [85.0, 60.0], "dpi": 300.0, "axes": [{
        "yscale": yscale, "xscale": "linear", "ylabel": ylabel, "xlim": [0, 1], "ylim": [0, 1],
        "texts": list(texts),
        "series": [{"kind": "bar", "values": [10.0, 20.0], "colors": ["#1b9e77"]},
                   {"kind": "errorbar", "yerr": list(yerr)}]}]}


def test_manifest_assertions():
    rec = _fig_record([(1, "f.png", _fig(texts=["***"]))])
    ok, _ = graders.grade({"type": "manifest", "file": "f.png", "assert": [
        {"path": "axes.0.series", "any": {"kind": "bar", "values": {"approx_set": [20, 10]}}},
        {"path": "axes.0.series", "any": {"kind": "bar", "colors": {"contains": "#1B9E77"}}},
        {"path": "axes.0.texts", "item_eq": "***"},
        {"path": "size_mm.0", "approx": 85, "rtol": 0.01},
        {"path": "axes.0.series", "count": {"field": "kind", "value": "errorbar", "eq": 1}}]}, rec, 1)
    assert ok
    ok, detail = graders.grade({"type": "manifest", "assert": [
        {"path": "axes.0.texts", "item_eq": "**"}]}, rec, 1)
    assert not ok, "** must not match ***"


def test_manifest_diff_flags_unrequested_changes():
    rec = _fig_record([(1, "f.png", _fig()), (2, "f.png", _fig(yscale="log")),
                       (3, "f.png", _fig(yscale="log", ylabel="changed", yerr=(5.0, 5.0)))])
    ok, _ = graders.grade({"type": "manifest_diff", "file": "f.png", "allowed": ["axes.0.yscale"],
                           "must_change": ["axes.0.yscale"]}, rec, 2)
    assert ok
    ok, detail = graders.grade({"type": "manifest_diff", "file": "f.png", "allowed": ["axes.0.ylabel"]}, rec, 3)
    assert not ok and "series" in detail


def test_immutable_detects_changed_bytes():
    rec = _fig_record([(1, "f.png", _fig()), (2, "f.png", _fig())])
    assert graders.grade({"type": "immutable"}, rec, 2)[0]
    rec.artifacts[0].served_sha256 = "f" * 64
    assert not graders.grade({"type": "immutable"}, rec, 2)[0]


def test_file_meta_on_real_matplotlib_output():
    mpl = pytest.importorskip("matplotlib")
    mpl.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(85 / 25.4, 60 / 25.4))
    ax.plot([1, 2, 3])
    ax.set_xlabel("dose")
    png, pdf = io.BytesIO(), io.BytesIO()
    fig.savefig(png, format="png", dpi=300)
    fig.savefig(pdf, format="pdf")
    plt.close(fig)
    m = png_meta(png.getvalue())
    assert m["dpi"] == 300.0 and abs(m["width_mm"] - 85) < 0.5
    p = file_meta("x.pdf", pdf.getvalue())
    assert p["vector"] and p["fonts_embedded"] and abs(p["width_mm"] - 85) < 0.5


def test_outputs_match():
    assert outputs_match("p.csv", b"a,1.0000001\n", b"a,1.0000002\n")
    assert not outputs_match("p.csv", b"a,1.0\n", b"a,1.1\n")
    assert not outputs_match("p.bin", b"x", b"y")


def test_reproduces_grader():
    case = BY_ID["s5_fit"]
    rec = _record(case)
    c = {"type": "reproduces", "outputs": ["fit_params.csv", "fit.png"]}
    assert not graders.grade(c, rec, 1)[0]
    rec.repro = {"ran": True, "outputs": {"fit_params.csv": True, "fit.png": True}}
    assert graders.grade(c, rec, 1)[0]
    rec.repro["outputs"]["fit.png"] = False
    assert not graders.grade(c, rec, 1)[0]


def test_grade_record_scores_unreached_turns_as_failures():
    case = BY_ID["s3_bars"]
    rec = _record(case)
    rec.turns = rec.turns[:2]
    rec.error = "RuntimeError: chat POST 502"
    g = grade_record(case, rec)
    assert not g["pass"] and 0 <= g["score"] < 1
    assert any("not reached" in c["detail"] for c in g["checks"])


# -- determinism --------------------------------------------------------------

def _close_json(a, b, rtol=1e-4):
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_close_json(a[k], b[k], rtol) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_close_json(x, y, rtol) for x, y in zip(a, b))
    if isinstance(a, float) and isinstance(b, (int, float)):
        return math.isclose(a, b, rel_tol=rtol, abs_tol=1e-9)
    return a == b


def test_fixtures_regenerate_identically():
    pytest.importorskip("openpyxl")
    import hashlib
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([sys.executable, str(LAB / "generate" / "build.py"), "--out", tmp],
                       check=True, capture_output=True)
        fresh = Path(tmp)
        stored = LAB / "cases"
        names = sorted(p.relative_to(stored).as_posix() for p in stored.rglob("*") if p.is_file())
        assert names == sorted(p.relative_to(fresh).as_posix() for p in fresh.rglob("*") if p.is_file())
        for name in names:
            a, b = (stored / name).read_bytes(), (fresh / name).read_bytes()
            if name.endswith("truth.json"):
                assert _close_json(json.loads(a), json.loads(b)), name
            elif a != b:
                assert hashlib.sha256(a).hexdigest() == hashlib.sha256(b).hexdigest(), name
