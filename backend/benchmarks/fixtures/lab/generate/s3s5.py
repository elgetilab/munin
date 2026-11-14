"""S3 iterative plot editing and S5 reproducibility.

S3 checks read the figure manifest (sandbox fighook) after every turn: what
was drawn, not what the code meant. Known gap: error bars drawn as plain
lines (seaborn >= 0.12 does this) are not recognised as error bars, so the
SD/SEM checks need matplotlib errorbar containers (ax.bar(yerr=...) or
ax.errorbar).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from scipy import stats

from common import csv_bytes, read_back, write_case


def _stars(p: float) -> str:
    # GraphPad style: ns >= 0.05, * < 0.05, ** < 0.01, *** < 0.001, **** < 0.0001
    for cut, s in ((1e-4, "****"), (1e-3, "***"), (1e-2, "**"), (5e-2, "*")):
        if p < cut:
            return s
    return "ns"


def s3_bars(root: Path):
    rng = np.random.default_rng(30001)
    genotypes = ["WT", "KO1", "KO2"]
    means = {("WT", "control"): 42, ("WT", "treated"): 47, ("KO1", "control"): 40, ("KO1", "treated"): 78,
             ("KO2", "control"): 18, ("KO2", "treated"): 22}
    rows = []
    for g in genotypes:
        for cond in ("control", "treated"):
            for rep in range(1, 5):
                rows.append([g, cond, rep, float(means[(g, cond)] * (1 + rng.normal(0, 0.12)))])
    data = csv_bytes(["genotype", "condition", "replicate", "activity_U_per_mg"], rows,
                     ["{}", "{}", "{}", "{:.2f}"])
    back = read_back(data)
    vals: dict = {}
    for g, c, _, v in back:
        vals.setdefault((g, c), []).append(float(v))
    m = {k: float(np.mean(v)) for k, v in vals.items()}
    sd = {k: float(np.std(v, ddof=1)) for k, v in vals.items()}
    sem = {k: sd[k] / np.sqrt(len(vals[k])) for k in vals}
    p_ko1 = float(stats.ttest_ind(vals[("KO1", "control")], vals[("KO1", "treated")], equal_var=False).pvalue)

    def by(cond, d):
        return [d[(g, cond)] for g in genotypes]
    bars_c = {"kind": "bar", "values": {"approx_set": by("control", m), "rtol": 0.01}}
    bars_t = {"kind": "bar", "values": {"approx_set": by("treated", m), "rtol": 0.01}}
    sd_c = {"kind": "errorbar", "yerr": {"approx_set": by("control", sd), "rtol": 0.02}}
    sem_c = {"kind": "errorbar", "yerr": {"approx_set": by("control", sem), "rtol": 0.02}}
    sem_t = {"kind": "errorbar", "yerr": {"approx_set": by("treated", sem), "rtol": 0.02}}
    S = "axes.0.series"
    turns = [
        {"prompt": "activity.csv has enzyme activity for three genotypes, control and treated, four "
                   "replicates each. Make a grouped bar chart of the mean activity per genotype with control "
                   "and treated side by side, error bars showing the SD. Save it as activity.png.",
         "checks": [{"type": "artifact", "pattern": r"activity\.png$"},
                    {"type": "manifest", "file": "activity.png", "assert": [
                        {"path": S, "any": bars_c}, {"path": S, "any": bars_t}, {"path": S, "any": sd_c}]}]},
        {"prompt": "Change the error bars to SEM instead of SD.",
         "checks": [{"type": "manifest", "file": "activity.png", "assert": [
                        {"path": S, "any": sem_c}, {"path": S, "any": sem_t}, {"path": S, "any": bars_c}]},
                    {"type": "manifest_diff", "file": "activity.png", "allowed": [S], "must_change": [S]}]},
        {"prompt": "Put the y axis on a log scale.",
         "checks": [{"type": "manifest", "file": "activity.png", "assert": [{"path": "axes.0.yscale", "eq": "log"}]},
                    {"type": "manifest_diff", "file": "activity.png", "allowed": ["axes.0.yscale"],
                     "must_change": ["axes.0.yscale"]}]},
        {"prompt": "Go back to a linear y axis. Label the y axis 'Activity (U/mg)' and colour the bars "
                   "#1b9e77 for control and #d95f02 for treated.",
         "checks": [{"type": "manifest", "file": "activity.png", "assert": [
                        {"path": "axes.0.yscale", "eq": "linear"},
                        {"path": "axes.0.ylabel", "eq": "Activity (U/mg)"},
                        {"path": S, "any": {"kind": "bar", "colors": {"contains": "#1b9e77"}}},
                        {"path": S, "any": {"kind": "bar", "colors": {"contains": "#d95f02"}}}]},
                    {"type": "manifest_diff", "file": "activity.png",
                     "allowed": ["axes.0.yscale", "axes.0.ylabel", S]}]},
        {"prompt": "Make the figure 85 mm wide and 60 mm tall and save it as a 300 dpi PNG.",
         "checks": [{"type": "manifest", "file": "activity.png", "assert": [
                        {"path": "size_mm.0", "approx": 85, "rtol": 0.02},
                        {"path": "size_mm.1", "approx": 60, "rtol": 0.02},
                        {"path": "dpi", "approx": 300, "rtol": 0.01}]},
                    {"type": "file_meta", "pattern": r"activity\.png$", "assert": [
                        {"path": "dpi", "approx": 300, "rtol": 0.01},
                        {"path": "width_mm", "approx": 85, "atol": 2},
                        {"path": "height_mm", "approx": 60, "atol": 2}]},
                    {"type": "manifest_diff", "file": "activity.png", "allowed": ["size_in", "size_mm", "dpi"]}]},
        {"prompt": "Add a significance bracket between control and treated for KO1, with asterisks from "
                   "a Welch's t-test (GraphPad style: * p<0.05, ** p<0.01, *** p<0.001, **** p<0.0001). "
                   "Report the p-value as p_ko1.",
         "checks": [{"type": "manifest", "file": "activity.png", "assert": [
                        {"path": "axes.0.texts", "item_eq": _stars(p_ko1)}]},
                    {"type": "numeric", "key": "p_ko1", "truth": "truth:p_ko1", "rtol": 0.02}],
         "results_keys": {"p_ko1": ""}},
        {"prompt": "Remove the KO2 genotype from the figure.",
         "checks": [{"type": "manifest", "file": "activity.png", "assert": [
                        {"path": S, "any": {"kind": "bar", "values": {"approx_set": [m[("WT", "control")], m[("KO1", "control")]], "rtol": 0.01}}}]},
                    {"type": "manifest_diff", "file": "activity.png", "allowed": [S, "axes.0.xticklabels"],
                     "must_change": [S]}]},
        {"prompt": "Export the final figure as a vector PDF with the fonts embedded, as activity.pdf.",
         "checks": [{"type": "artifact", "pattern": r"activity\.pdf$"},
                    {"type": "file_meta", "pattern": r"activity\.pdf$", "assert": [
                        {"path": "vector", "eq": True}, {"path": "fonts_embedded", "eq": True},
                        {"path": "width_mm", "approx": 85, "atol": 2}]},
                    {"type": "immutable"}]},
    ]
    truth = {"means": {f"{g}/{c}": v for (g, c), v in m.items()}, "sd": {f"{g}/{c}": v for (g, c), v in sd.items()},
             "sem": {f"{g}/{c}": v for (g, c), v in sem.items()}, "p_ko1": p_ko1, "stars_ko1": _stars(p_ko1)}
    write_case(root, "s3_bars", "S3", turns, files={"activity.csv": data},
               truth=truth, tags=["plot-editing"],
               provenance="synthetic, s3s5.s3_bars")


def s3_dose(root: Path):
    rng = np.random.default_rng(30002)
    doses = 10 ** np.arange(-2, 2.01, 0.5)
    rows = []
    for name, lec, top in (("cmpd-A", -0.4, 100.0), ("cmpd-B", 0.6, 85.0)):
        for d in doses:
            for rep in range(3):
                y = 2 + (top - 2) / (1 + 10 ** ((lec - np.log10(d)) * 1.1)) + rng.normal(0, 3)
                rows.append([name, float(d), rep + 1, float(y)])
    data = csv_bytes(["compound", "conc_uM", "replicate", "inhibition_pct"], rows, ["{}", "{:.4g}", "{}", "{:.2f}"])
    S = "axes.0.series"
    turns = [
        {"prompt": "dose.csv has percent inhibition for two compounds at nine concentrations, triplicates. "
                   "Plot mean ± SD against concentration for cmpd-A only, as points, and save as dose.png.",
         "checks": [{"type": "artifact", "pattern": r"dose\.png$"},
                    {"type": "manifest", "file": "dose.png", "assert": [{"path": S, "any": {"kind": "errorbar"}}]}]},
        {"prompt": "Use a logarithmic x axis.",
         "checks": [{"type": "manifest", "file": "dose.png", "assert": [{"path": "axes.0.xscale", "eq": "log"}]},
                    {"type": "manifest_diff", "file": "dose.png", "allowed": ["axes.0.xscale"],
                     "must_change": ["axes.0.xscale"]}]},
        {"prompt": "Add cmpd-B to the same plot in a different colour, with a legend.",
         "checks": [{"type": "manifest", "file": "dose.png", "assert": [
                        {"path": S, "count": {"field": "kind", "value": "errorbar", "eq": 2}}]}]},
        {"prompt": "Label the x axis 'Concentration (µM)' and the y axis 'Inhibition (%)', and set the y "
                   "axis from 0 to 110.",
         "checks": [{"type": "manifest", "file": "dose.png", "assert": [
                        {"path": "axes.0.xlabel", "eq": "Concentration (µM)"},
                        {"path": "axes.0.ylabel", "eq": "Inhibition (%)"},
                        {"path": "axes.0.ylim.0", "approx": 0, "atol": 0.5},
                        {"path": "axes.0.ylim.1", "approx": 110, "atol": 0.5}]},
                    {"type": "manifest_diff", "file": "dose.png", "allowed": ["axes.0.xlabel", "axes.0.ylabel"]}]},
        {"prompt": "Save it once more as dose.svg as well.",
         "checks": [{"type": "artifact", "pattern": r"dose\.svg$"}, {"type": "immutable"}]},
    ]
    write_case(root, "s3_dose", "S3", turns, files={"dose.csv": data}, truth={}, tags=["plot-editing"],
               provenance="synthetic, s3s5.s3_dose")


def s5_cases(root: Path):
    rng = np.random.default_rng(50001)
    rows = []
    for d in 10 ** np.arange(-3, 2.01, 0.5):
        for _ in range(3):
            y = 3 + 95 / (1 + 10 ** ((-0.8 - np.log10(d)) * 1.3)) + rng.normal(0, 3)
            rows.append([float(d), float(y)])
    fit_csv = csv_bytes(["conc_uM", "response"], rows, ["{:.6g}", "{:.3f}"])
    prompt = ("repro_dose.csv is a dose-response experiment. Fit a four-parameter logistic against "
              "log10(concentration), save the fitted parameters to fit_params.csv (columns parameter, "
              "value) and a plot of the data with the fitted curve as fit.png.")
    checks = [{"type": "bundle"}, {"type": "reproduces", "outputs": ["fit_params.csv", "fit.png"]}]
    write_case(root, "s5_fit", "S5", [{"prompt": prompt, "checks": checks}], files={"repro_dose.csv": fit_csv},
               truth={}, tags=["reproducibility"], provenance="synthetic, s3s5.s5_cases")
    rows = [[g, float(v)] for g, mu in (("A", 10), ("B", 14), ("C", 9)) for v in rng.normal(mu, 2, 6)]
    grp_csv = csv_bytes(["group", "value"], rows, ["{}", "{:.3f}"])
    prompt = ("groups.csv has three groups. Run a one-way ANOVA followed by Tukey's multiple comparisons, "
              "write the comparison table to tukey.csv, and save a box plot with the individual points "
              "as groups.png.")
    checks = [{"type": "bundle"}, {"type": "reproduces", "outputs": ["tukey.csv", "groups.png"]}]
    write_case(root, "s5_anova", "S5", [{"prompt": prompt, "checks": checks}], files={"groups.csv": grp_csv},
               truth={}, tags=["reproducibility"], provenance="synthetic, s3s5.s5_cases")


def build(root: Path) -> None:
    s3_bars(root)
    s3_dose(root)
    s5_cases(root)
