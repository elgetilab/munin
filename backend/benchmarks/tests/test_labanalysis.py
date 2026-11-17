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


def test_manifest_diff_ignores_series_order():
    # A redraw can list the errorbar container before the bars (smoke run 2026-11-14).
    reordered = _fig(yscale="log")
    reordered["axes"][0]["series"].reverse()
    rec = _fig_record([(1, "f.png", _fig()), (2, "f.png", reordered)])
    ok, detail = graders.grade({"type": "manifest_diff", "file": "f.png",
                                "allowed": ["axes.0.yscale"]}, rec, 2)
    assert ok, detail


def test_manifest_diff_bar_yerr_copy_is_not_a_change():
    # bar(yerr=) also stores the error on the bar; ax.errorbar() does not.
    via_bar = _fig()
    via_bar["axes"][0]["series"][0]["yerr"] = [1.0, 2.0]
    rec = _fig_record([(1, "f.png", _fig()), (2, "f.png", via_bar)])
    ok, detail = graders.grade({"type": "manifest_diff", "file": "f.png", "allowed": []}, rec, 2)
    assert ok, detail


# Manifest shapes below are from the overflow-check run (2026-11, s3_bars and s3_dose).

def _bar(label, pos, vals, color, **err):
    return {"kind": "bar", "orientation": "vertical", "label": label, "positions": pos,
            "values": vals, "colors": [color], **err}


def _per_bar_fig():
    # KO2 removed, one ax.bar call per bar (s3_bars#1 turn 7, activity.png v37)
    S = [_bar("control", [0.0], [41.985], "#1b9e77"), {"kind": "errorbar", "label": None, "yerr": [3.68]},
         _bar("treated", [0.35], [44.5425], "#d95f02"), {"kind": "errorbar", "label": None, "yerr": [1.77]},
         _bar(None, [0.7], [37.54], "#1b9e77"), {"kind": "errorbar", "label": None, "yerr": [3.62]},
         _bar(None, [1.05], [70.075], "#d95f02"), {"kind": "errorbar", "label": None, "yerr": [2.65]}]
    return {"axes": [{"series": S}]}


def test_bar_values_match_across_per_bar_series():
    ko2 = {"type": "manifest", "file": "activity.png", "assert": [{"path": "axes.0.series", "any": {
        "kind": "bar", "values": {"approx_set": [41.985, 37.54], "rtol": 0.01}}}]}
    rec = _fig_record([(7, "activity.png", _per_bar_fig())])
    ok, detail = graders.grade(ko2, rec, 7)
    assert ok, detail
    wrong = _per_bar_fig()
    wrong["axes"][0]["series"][4]["values"] = [19.595]          # KO2 kept, KO1 dropped
    ok, _ = graders.grade(ko2, _fig_record([(7, "activity.png", wrong)]), 7)
    assert not ok
    # colours are not mixed: control + treated values of one genotype do not pass
    ok, _ = graders.grade({**ko2, "assert": [{"path": "axes.0.series", "any": {
        "kind": "bar", "values": {"approx_set": [41.985, 44.5425], "rtol": 0.01}}}]},
        _fig_record([(7, "activity.png", _per_bar_fig())]), 7)
    assert not ok


def test_must_change_satisfied_when_already_requested():
    # s3_dose#2: the turn-1 plot already used a log x axis
    def dose(xscale, title=None):
        return {"axes": [{"xscale": xscale, "title": title, "series": [
            {"kind": "errorbar", "label": None, "x": [0.01, 0.1], "y": [3.0, 9.0], "yerr": [1.0, 2.0]}]}]}
    c = {"type": "manifest_diff", "file": "dose.png", "allowed": ["axes.0.xscale"],
         "must_change": ["axes.0.xscale"], "requested": {"axes.0.xscale": "log"}}
    ok, detail = graders.grade(c, _fig_record([(1, "dose.png", dose("log")), (2, "dose.png", dose("log"))]), 2)
    assert ok and "already 'log'" in detail
    # nothing redrawn at all is fine too when the request already held
    ok, detail = graders.grade(c, _fig_record([(1, "dose.png", dose("log"))]), 2)
    assert ok and "already" in detail
    # still linear: unchanged and not what was asked
    ok, detail = graders.grade(c, _fig_record([(1, "dose.png", dose("linear")),
                                               (2, "dose.png", dose("linear"))]), 2)
    assert not ok and "unchanged" in detail
    # an unrequested title change still fails
    ok, detail = graders.grade(c, _fig_record([(1, "dose.png", dose("log")),
                                               (2, "dose.png", dose("log", "Dose response"))]), 2)
    assert not ok and "axes.0.title" in detail


def test_manifest_diff_ignores_float_noise():
    # s3_dose turn 4: means recomputed from the CSV differ in the last bits
    def fig(y, title=None):
        return {"axes": [{"title": title, "series": [
            {"kind": "errorbar", "label": "cmpd-A", "x": [0.01, 0.1], "y": y, "yerr": [1.0, 2.0]}]}]}
    c = {"type": "manifest_diff", "file": "dose.png", "allowed": []}
    rec = _fig_record([(1, "dose.png", fig([3.3333333333333335, 9.0])),
                       (2, "dose.png", fig([3.333333333333333, 9.000000000000002]))])
    ok, detail = graders.grade(c, rec, 2)
    assert ok, detail
    rec = _fig_record([(1, "dose.png", fig([3.33, 9.0])), (2, "dose.png", fig([3.40, 9.0]))])
    assert not graders.grade(c, rec, 2)[0]
    rec = _fig_record([(1, "dose.png", fig([3.33, 9.0])), (2, "dose.png", fig([3.33, 9.0], "T"))])
    ok, detail = graders.grade(c, rec, 2)
    assert not ok and "title" in detail


def test_renamed_output_is_reported():
    # s3_bars#2 turn 7: the edit went to activity_significance.png
    rec = _fig_record([(5, "activity.png", _fig()), (6, "plot_101_1.png", _fig(ylabel="z")),
                       (6, "activity_significance.png", _fig(texts=["***"]))])
    ok, detail = graders.grade({"type": "manifest_diff", "file": "activity.png", "allowed": []}, rec, 6)
    assert not ok and detail == "output file renamed: activity.png -> activity_significance.png"
    ok, detail = graders.grade({"type": "manifest", "file": "activity.png", "assert": [
        {"path": "axes.0.texts", "item_eq": "***"}]}, rec, 6)
    assert not ok and "output file renamed" in detail
    # only an inline capture: no rename claimed
    rec = _fig_record([(5, "activity.png", _fig()), (6, "plot_101_1.png", _fig(ylabel="z"))])
    ok, detail = graders.grade({"type": "manifest_diff", "file": "activity.png", "allowed": []}, rec, 6)
    assert not ok and detail == "no new figure this turn"


def test_canon_series_dedupes_asymmetric_and_x_errors():
    lo, hi = [1.0, 2.0], [1.5, 2.5]
    plain = [_bar("c", [0, 1], [10.0, 20.0], "#1b9e77"),
             {"kind": "errorbar", "label": None, "yerr_low": lo, "yerr_high": hi}]
    copy_on_bar = copy.deepcopy(plain)
    copy_on_bar[0].update(yerr_low=lo, yerr_high=hi)
    assert graders._canon_series(copy_on_bar) == plain
    xe = [_bar("c", [0, 1], [10.0, 20.0], "#1b9e77", xerr=[0.1, 0.2]),
          {"kind": "errorbar", "label": None, "xerr": [0.1, 0.2]}]
    assert "xerr" not in graders._canon_series(xe)[0]
    # different errors on the bar are kept (a real difference)
    other = copy.deepcopy(plain)
    other[0].update(yerr_low=lo, yerr_high=[9.0, 9.0])
    assert "yerr_low" in graders._canon_series(other)[0]
    rec = _fig_record([(1, "f.png", {"axes": [{"series": plain}]}),
                       (2, "f.png", {"axes": [{"series": list(reversed(copy_on_bar))}]})])
    ok, detail = graders.grade({"type": "manifest_diff", "file": "f.png", "allowed": []}, rec, 2)
    assert ok, detail


def test_errorbar_series_order_is_content_based():
    # two errorbar series with no values/positions: swapping them is not a change
    e1 = {"kind": "errorbar", "label": None, "yerr": [3.68, 3.62, 1.01]}
    e2 = {"kind": "errorbar", "label": None, "yerr": [1.77, 2.65, 1.23]}
    rec = _fig_record([(1, "f.png", {"axes": [{"series": [e1, e2]}]}),
                       (2, "f.png", {"axes": [{"series": [e2, e1]}]})])
    ok, detail = graders.grade({"type": "manifest_diff", "file": "f.png", "allowed": []}, rec, 2)
    assert ok, detail


def test_s3_bars_turn1_accepts_seaborn_shaped_manifest():
    # after the hook change seaborn bars come with a separate errorbar series
    c = BY_ID["s3_bars"].turns[0].checks[1]
    sd_c, sd_t = [7.361451396746884, 7.232403934147116, 2.020140259156939], [3.54, 5.3, 2.47]
    fig = {"axes": [{"series": [
        _bar("control", [-0.2, 0.8, 1.8], [41.985, 37.54, 19.595], "#1f77b4"),
        _bar("treated", [0.2, 1.2, 2.2], [44.5425, 70.075, 21.97], "#ff7f0e"),
        {"kind": "errorbar", "label": None, "yerr": sd_c},
        {"kind": "errorbar", "label": None, "yerr": sd_t}]}]}
    ok, detail = graders.grade(c, _fig_record([(1, "activity.png", fig)]), 1)
    assert ok, detail
    fig["axes"][0]["series"][2] = {"kind": "errorbar", "label": None,
                                   "yerr_low": sd_c, "yerr_high": [2 * x for x in sd_c]}
    assert not graders.grade(c, _fig_record([(1, "activity.png", fig)]), 1)[0], "asymmetric is not SD"


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


def test_manifest_diff_ignores_saved_size_keys():
    # The new hook reports the saved file's size; a relabelled axis under a
    # tight bbox changes it without being an edit of its own.
    def fig(saved, ylabel="y"):
        return {"size_mm": [85, 60], "size_mm_saved": saved, "size_differs_on_save": saved != [85, 60],
                "axes": [{"ylabel": ylabel, "series": []}]}
    c = {"type": "manifest_diff", "file": "f.png", "allowed": ["axes.0.ylabel"]}
    rec = _fig_record([(1, "f.png", fig([85, 60])), (2, "f.png", fig([88.1, 61.0], "Activity"))])
    ok, detail = graders.grade(c, rec, 2)
    assert ok, detail


def test_text_checks_ignore_lookalike_codepoints():
    # bugfix live check: the case asks for "Concentration (µM)" (micro sign
    # U+00B5); the model wrote Greek mu U+03BC, which renders the same.
    fig = {"axes": [{"xlabel": "Concentration (μM)", "series": []}]}
    rec = _fig_record([(1, "dose.png", fig)])
    ok, detail = graders.grade({"type": "manifest", "file": "dose.png", "assert": [
        {"path": "axes.0.xlabel", "eq": "Concentration (µM)"}]}, rec, 1)
    assert ok, detail
    ok, _ = graders.grade({"type": "manifest", "file": "dose.png", "assert": [
        {"path": "axes.0.xlabel", "eq": "Concentration (nM)"}]}, rec, 1)
    assert not ok


# --- runner: --concurrency and --resume -----------------------------------------

class _FakeLabClient:
    """Stands in for LabClient: no network, records how many runs overlap."""
    calls: list = []
    in_flight = 0
    peak = 0

    def __init__(self, base=None, persona=None):
        pass

    def run_case(self, case, rep, keep=False, sandbox_image=None):
        import threading
        import time
        cls = _FakeLabClient
        with _FAKE_LOCK:
            cls.in_flight += 1
            cls.peak = max(cls.peak, cls.in_flight)
            cls.calls.append((case.id, rep, threading.get_ident()))
        time.sleep(0.05)
        with _FAKE_LOCK:
            cls.in_flight -= 1
        return RunRecord(case_id=case.id, suite=case.suite, rep=rep), None

    def close(self):
        pass


import threading as _threading  # noqa: E402
_FAKE_LOCK = _threading.Lock()


def _run_main(monkeypatch, tmp_path, *extra):
    from munin_bench.labanalysis import client as client_mod, run as run_mod
    monkeypatch.setattr(run_mod, "BENCH", tmp_path)
    monkeypatch.setattr(client_mod, "LabClient", _FakeLabClient)
    _FakeLabClient.calls, _FakeLabClient.in_flight, _FakeLabClient.peak = [], 0, 0
    rc = run_mod.main(["--tag", "t", "--no-scorecard", "--case", "s1_4pl_s1",
                       "--case", "s1_decay1_s1", "--reps", "2", *extra])
    rec = (tmp_path / "results" / "lab" / "t" / "records.jsonl").read_text().splitlines()
    graded = json.loads((tmp_path / "results" / "lab" / "t" / "graded.json").read_text())
    return rc, rec, graded


def test_runner_concurrency_runs_in_parallel_and_records_every_run(monkeypatch, tmp_path):
    rc, rec, graded = _run_main(monkeypatch, tmp_path, "--concurrency", "3")
    assert rc == 0 and len(rec) == 4 and len(graded) == 4
    assert _FakeLabClient.peak > 1, "runs did not overlap"
    assert {(json.loads(r)["case_id"], json.loads(r)["rep"]) for r in rec} == {
        ("s1_4pl_s1", 1), ("s1_4pl_s1", 2), ("s1_decay1_s1", 1), ("s1_decay1_s1", 2)}


def test_runner_resume_skips_recorded_runs_and_grades_the_whole_file(monkeypatch, tmp_path):
    _run_main(monkeypatch, tmp_path)
    p = tmp_path / "results" / "lab" / "t" / "records.jsonl"
    lines = p.read_text().splitlines()
    p.write_text("\n".join(lines[:3]) + "\n")          # an interrupted run
    rc, rec, graded = _run_main(monkeypatch, tmp_path, "--resume", "--concurrency", "2")
    assert [(c[0], c[1]) for c in _FakeLabClient.calls] == [("s1_decay1_s1", 2)]
    assert rc == 0 and len(rec) == 4 and len(graded) == 4
