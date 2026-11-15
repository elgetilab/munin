"""
Unit checks for the figure hook (app/fighook.py), run in-process under Agg.

The hook source is exec'd the way the kernel runs it, and its published
descriptions are captured by patching IPython's publish_display_data. Needs the
sandbox image's plotting stack (matplotlib, seaborn, pandas), so run it inside
a throwaway container of the image, never against production:

    docker run --rm --network none -v "$PWD/backend/sandbox/tests:/t:ro" \\
        munin-sandbox:ci-live python /t/test_fighook.py

backend/scripts/ci/run_sandbox_live_tests.sh does this. With pytest installed
it also runs as `pytest test_fighook.py`.

Covers: seaborn error bars (sd, se, ci, hue, horizontal, capsize,
errorbar=None, pointplot, lineplot band), what must stay as it was (an
unrelated fill_between, an axvline through a bar centre, plt.bar(yerr=)),
and the layout and size reports (no clipped warning for ticks outside the
axis limits, no overlap warning from measuring at the wrong dpi, the real
size of a bbox_inches='tight' file).
"""

from __future__ import annotations

import os
import sys
import tempfile

import matplotlib

matplotlib.use("Agg")

try:
    import pytest
except ImportError:  # the sandbox image has no pytest; see __main__ below
    pytest = None

if pytest is not None:
    sns = pytest.importorskip("seaborn")
else:
    import seaborn as sns

import IPython.display
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

FIGURE_MIME = "application/vnd.munin.figure+json"
_HERE = os.path.dirname(os.path.abspath(__file__))
_HOOK_CANDIDATES = [
    os.environ.get("FIGHOOK_PATH", ""),
    os.path.join(_HERE, "..", "app", "fighook.py"),
    "/app/app/fighook.py",
]

_published: list[dict] = []


def _capture(data, metadata=None, **_kw):
    if FIGURE_MIME in data:
        _published.append(data[FIGURE_MIME])


IPython.display.publish_display_data = _capture


def _install_hook():
    for path in _HOOK_CANDIDATES:
        if path and os.path.exists(path):
            src = open(path).read()
            break
    else:
        raise RuntimeError(f"fighook.py not found in {_HOOK_CANDIDATES}")
    ns = {"__name__": "_munin_fighook"}
    exec(compile(src, "<munin-figure-hook>", "exec"), ns)
    assert ns["_munin_describe_figure"] is not None, "hook failed to install"


_install_hook()
_TMP = tempfile.mkdtemp(prefix="fighook-test-")


def save(fig, name="fig.png", **kw) -> dict:
    """Save through the hooked savefig and return the published description."""
    _published.clear()
    path = os.path.join(_TMP, name)
    fig.savefig(path, **kw)
    plt.close(fig)
    assert len(_published) == 1, _published
    desc = _published[0]
    desc["_path"] = path
    return desc


def series(desc, kind=None, ax=0):
    out = desc["axes"][ax]["series"]
    return [s for s in out if kind is None or s.get("kind") == kind]


def close(a, b, tol=1e-6):
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    return a.shape == b.shape and np.allclose(a, b, rtol=tol, atol=tol)


ERR_KEYS = ("yerr", "yerr_low", "yerr_high", "xerr", "xerr_low", "xerr_high")


def has_err(s):
    return any(k in s for k in ERR_KEYS)


# Three genotypes x two doses, four replicates each; uneven spread on purpose.
_rng = np.random.default_rng(7)
DF = pd.DataFrame([
    {"genotype": g, "dose": d, "activity": m + _rng.normal(0, sd)}
    for g, base in (("WT", 10.0), ("KO1", 6.0), ("KO2", 3.0))
    for d, m, sd in (("low", base, 0.5), ("high", base * 1.5, 1.5))
    for _ in range(4)
])
ORDER = ["WT", "KO1", "KO2"]
HUE = ["low", "high"]


def stats(by):
    g = DF.groupby(by)["activity"]
    return g.mean(), g.std(ddof=1), g.sem(ddof=1)


# ---- seaborn bars --------------------------------------------------------------

def _bar_plot(**kw):
    fig, ax = plt.subplots()
    sns.barplot(DF, ax=ax, order=ORDER, **kw)
    return save(fig)


def test_barplot_sd():
    mean, sd, _ = stats("genotype")
    d = _bar_plot(x="genotype", y="activity", errorbar="sd")
    (bar,) = series(d, "bar")
    assert close(bar["values"], mean[ORDER]), bar
    assert close(bar["yerr"], sd[ORDER]), bar
    eb = series(d, "errorbar")
    assert len(eb) == 1 and close(eb[0]["yerr"], sd[ORDER]), eb
    # The stems are claimed, not listed again as loose lines.
    assert not series(d, "line"), series(d)


def test_barplot_se():
    _, _, se = stats("genotype")
    d = _bar_plot(x="genotype", y="activity", errorbar="se")
    (bar,) = series(d, "bar")
    assert close(bar["yerr"], se[ORDER]), bar


def test_barplot_ci_is_asymmetric():
    mean, _, _ = stats("genotype")
    d = _bar_plot(x="genotype", y="activity", errorbar=("ci", 95), seed=0)
    (bar,) = series(d, "bar")
    assert "yerr_low" in bar and "yerr_high" in bar, bar
    lo = np.asarray(bar["yerr_low"])
    hi = np.asarray(bar["yerr_high"])
    assert (lo > 0).all() and (hi > 0).all() and not np.allclose(lo, hi), bar
    for g, m, l, h in zip(ORDER, mean[ORDER], lo, hi):
        vals = DF.loc[DF.genotype == g, "activity"]
        assert vals.min() - 1e-9 <= m - l and m + h <= vals.max() + 1e-9


def test_barplot_hue_sd():
    mean, sd, _ = stats(["genotype", "dose"])
    d = _bar_plot(x="genotype", y="activity", hue="dose", hue_order=HUE, errorbar="sd")
    bars = series(d, "bar")
    assert len(bars) == 2, bars
    for b, dose in zip(bars, HUE):
        idx = [(g, dose) for g in ORDER]
        assert close(b["values"], mean[idx]), b
        assert close(b["yerr"], sd[idx]), b


def test_barplot_horizontal():
    mean, sd, _ = stats(["genotype", "dose"])
    d = _bar_plot(y="genotype", x="activity", hue="dose", hue_order=HUE, errorbar="sd")
    bars = series(d, "bar")
    assert len(bars) == 2 and all(b["orientation"] == "horizontal" for b in bars), bars
    for b, dose in zip(bars, HUE):
        idx = [(g, dose) for g in ORDER]
        assert "yerr" not in b and close(b["xerr"], sd[idx]), b


def test_barplot_capsize():
    mean, sd, _ = stats(["genotype", "dose"])
    d = _bar_plot(x="genotype", y="activity", hue="dose", hue_order=HUE,
                  errorbar="sd", capsize=0.2)
    for b, dose in zip(series(d, "bar"), HUE):
        assert close(b["yerr"], sd[[(g, dose) for g in ORDER]]), b
    assert not series(d, "line"), series(d)


def test_barplot_no_errorbar():
    d = _bar_plot(x="genotype", y="activity", hue="dose", hue_order=HUE, errorbar=None)
    assert len(series(d, "bar")) == 2
    assert not any(has_err(s) for s in series(d)), series(d)
    assert not series(d, "errorbar")


def test_pointplot():
    mean, sd, _ = stats(["genotype", "dose"])
    fig, ax = plt.subplots()
    sns.pointplot(DF, x="genotype", y="activity", hue="dose", order=ORDER, hue_order=HUE,
                  errorbar="sd", ax=ax)
    d = save(fig)
    eb = series(d, "errorbar")
    assert len(eb) == 2, series(d)
    for s, dose in zip(eb, HUE):
        idx = [(g, dose) for g in ORDER]
        assert close(s["y"], mean[idx]) and close(s["yerr"], sd[idx]), s
    assert not series(d, "line"), series(d)  # stems claimed, legend proxies skipped


def test_pointplot_capsize():
    mean, sd, _ = stats("genotype")
    fig, ax = plt.subplots()
    sns.pointplot(DF, x="genotype", y="activity", order=ORDER, errorbar="sd", capsize=0.2, ax=ax)
    d = save(fig)
    (eb,) = series(d, "errorbar")
    assert close(eb["yerr"], sd[ORDER]), eb


def test_lineplot_band():
    t = DF.assign(t=DF.groupby("genotype").cumcount())
    fig, ax = plt.subplots()
    sns.lineplot(t, x="t", y="activity", errorbar="sd", ax=ax)
    d = save(fig)
    g = t.groupby("t")["activity"]
    (line,) = series(d, "line")
    assert line.get("error_style") == "band", line
    assert close(line["y"], g.mean()) and close(line["yerr"], g.std(ddof=1)), line
    assert not series(d, "fill"), series(d)  # the band is part of the line now


def test_unrelated_fill_stays_fill():
    x = np.linspace(0, 1, 20)
    fig, ax = plt.subplots()
    ax.plot(x, x, color="C0", label="fit")
    ax.fill_between(x, x + 5, x + 6, color="C0", alpha=0.3)   # same colour, not around it
    ax.fill_between(x, -1, 2, color="C1", alpha=0.3)          # around it, other colour
    d = save(fig)
    (line,) = series(d, "line")
    assert not has_err(line) and "error_style" not in line, line
    assert len(series(d, "fill")) == 2, series(d)


def test_axvline_at_bar_centre_is_not_an_error_bar():
    fig, ax = plt.subplots()
    ax.bar([0, 1], [3.0, 4.0])
    ax.axvline(0, ymin=0.2, ymax=0.8)
    ax.plot([1, 1], [1.0, 2.0])  # through a bar centre, but below its tip
    d = save(fig)
    (bar,) = series(d, "bar")
    assert not has_err(bar) and not series(d, "errorbar"), series(d)
    assert len(series(d, "line")) == 2, series(d)


def test_plt_bar_yerr_unchanged():
    fig, ax = plt.subplots()
    ax.bar(["ctrl", "drug A", "drug B"], [5.0, 7.5, 2.5], yerr=[0.5, 0.75, 0.25], color="#999999")
    d = save(fig)
    s = series(d)
    assert [x["kind"] for x in s] == ["errorbar", "bar"], s  # containers in creation order
    eb, bar = s
    assert bar == {
        "kind": "bar", "orientation": "vertical", "label": None,
        "positions": [0.0, 1.0, 2.0], "values": [5.0, 7.5, 2.5], "colors": ["#999999"],
        "yerr": bar["yerr"],
    }, bar
    assert close(bar["yerr"], [0.5, 0.75, 0.25]) and close(eb["yerr"], [0.5, 0.75, 0.25]), s
    assert "x" not in eb and "error_style" not in eb


# ---- layout and size -----------------------------------------------------------

def test_no_clipped_warning_for_ticks_outside_limits():
    fig, ax = plt.subplots(layout="constrained")
    ax.plot([0, 1], [88, 99])
    ax.set_ylim(87.7, 99.5)
    locs = ax.yaxis.get_majorticklocs()
    assert (locs < 87.7).any() or (locs > 99.5).any(), locs  # precondition
    d = save(fig)
    assert d["layout"]["clipped_text"] == [], d["layout"]

    fig, ax = plt.subplots(layout="constrained")
    ax.loglog([0.15, 7], [0.2, 8])
    d = save(fig)
    assert d["layout"]["clipped_text"] == [], d["layout"]


def test_clipped_text_still_found():
    fig, ax = plt.subplots(figsize=(3, 2))
    ax.plot([0, 1], [0, 1])
    ax.set_title("A very long title that certainly does not fit into a figure this narrow at all")
    d = save(fig)
    assert any(t.startswith("A very long title") for t in d["layout"]["clipped_text"]), d["layout"]


def _labelled_bars():
    fig, ax = plt.subplots(figsize=(85 / 25.4, 60 / 25.4), dpi=100)
    ax.bar(["ctrl", "KO1", "KO2", "rescue"], [5, 7, 3, 6])
    ax.set_ylabel("activity (U/mg)")
    ax.set_title("Enzyme activity")
    return fig


def test_no_false_overlap_at_other_dpi():
    # Measured at the figure's 100 dpi after a 150 dpi save, the y label
    # appeared to overlap the tick labels.
    for dpi in (100, 150, 300):
        d = save(_labelled_bars(), dpi=dpi)
        assert d["layout"]["overlapping_text"] == [], (dpi, d["layout"])
        assert d["dpi"] == dpi and d["dpi_saved"] == dpi


def test_real_overlap_still_found():
    fig, ax = plt.subplots()
    ax.text(0.5, 0.5, "label one")
    ax.text(0.5, 0.5, "label two")
    d = save(fig, dpi=150)
    assert ["label one", "label two"] in d["layout"]["overlapping_text"], d["layout"]


def test_saved_size_plain():
    d = save(_labelled_bars(), dpi=300)
    assert close(d["size_mm"], [85, 60]) and close(d["size_mm_saved"], [85, 60]), d
    assert d["size_differs_on_save"] is False
    h, w = plt.imread(d["_path"]).shape[:2]
    assert d["size_px_saved"] == [w, h], (d["size_px_saved"], w, h)


def test_saved_size_tight_bbox():
    fig = _labelled_bars()
    fig.axes[0].set_xlabel("genotype")
    d = save(fig, dpi=150, bbox_inches="tight")
    assert close(d["size_mm"], [85, 60]), d["size_mm"]          # what figsize asked for
    assert d["size_differs_on_save"] is True, d
    h, w = plt.imread(d["_path"]).shape[:2]
    assert d["size_px_saved"] == [w, h], (d["size_px_saved"], w, h)
    assert close(d["size_mm_saved"], [w / 150 * 25.4, h / 150 * 25.4], tol=0.01), d["size_mm_saved"]
    # The tight page contains every label: nothing is clipped.
    assert d["layout"]["clipped_text"] == [], d["layout"]


def test_saved_size_tight_pdf():
    d = save(_labelled_bars(), name="fig.pdf", bbox_inches="tight")
    assert d["format"] == "pdf" and "size_px_saved" not in d
    assert d["size_differs_on_save"] is True
    with open(d["_path"], "rb") as f:
        head = f.read().decode("latin-1")
    import re
    box = re.search(r"/MediaBox \[\s*0 0 ([\d.]+) ([\d.]+)\s*\]", head)
    assert box, "no MediaBox"
    w_mm, h_mm = (float(v) / 72 * 25.4 for v in box.groups())
    assert abs(w_mm - d["size_mm_saved"][0]) < 0.1 and abs(h_mm - d["size_mm_saved"][1]) < 0.1, (
        w_mm, h_mm, d["size_mm_saved"])


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"[PASS] {name}")
        except Exception as exc:
            failed += 1
            print(f"[FAIL] {name} - {type(exc).__name__}: {str(exc)[:600]}")
        finally:
            plt.close("all")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
