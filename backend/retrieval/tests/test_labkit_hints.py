"""Tests for the labkit pointers (docs/agent-track/LAB-ADOPTION-PLAN.md).

Why: in Phase 4 run 1 the model imported labkit in 16 of 285 lab runs and
fitted by hand with scipy/lmfit in the rest; one-phase decay then reported
the drop as Y0 in 10 runs, a convention labkit.fit gets right. run_python
now adds a `labkit_hint` when a script hand-fits a model labkit has, and an
attached file in a format labkit reads gets the reader named in its note.
The hints must stay narrow: general optimisation and plotting get nothing,
and each hint is given once per conversation.

Run standalone or under pytest:
    python backend/retrieval/tests/test_labkit_hints.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("CHATS_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="munin-test-lkh-"), "chats.db"))

import labkit_hints  # noqa: E402
from labkit_hints import HintState, fit_hint, upload_hint  # noqa: E402

# The model's own script from Phase 4 run 1 (s1_decay1, fresh set, run 3),
# trimmed: an lmfit one-phase decay whose "Y0" is really the span.
REAL_DECAY = '''import numpy as np
import pandas as pd
from lmfit import Model, Parameters

df = pd.read_csv('inputs/decay_7.csv')
t = df['time_min'].values
y = df['signal'].values

# One-phase decay to non-zero plateau: y = Y0 * exp(-k*t) + plateau
def decay_model(t, Y0, k, plateau):
    return Y0 * np.exp(-k * t) + plateau

model = Model(decay_model)
params = Parameters()
params.add('Y0', value=np.max(y) - np.min(y), min=0)
params.add('k', value=0.1, min=0)
params.add('plateau', value=np.min(y), min=0)
result = model.fit(y, params, t=t)
print(result.fit_report())
'''

FOUR_PL = '''from scipy.optimize import curve_fit
import numpy as np
def four_pl(x, bottom, top, logec50, hill):
    return bottom + (top - bottom) / (1 + 10 ** ((logec50 - x) * hill))
popt, pcov = curve_fit(four_pl, x, y, p0=[0, 100, -7, 1])
'''

MM = '''from scipy.optimize import curve_fit
def mm(s, vmax, km):
    return vmax * s / (km + s)
popt, pcov = curve_fit(mm, s, v)
'''

# The model's own read in Phase 4 run 1 (s2_bruker_1d, all three runs).
RAW_FID = '''import numpy as np
raw = np.fromfile('inputs/bruker_1h/exp10/fid', dtype='<i2')
'''

BRUKER = '''import nmrglue as ng
dic, data = ng.bruker.read('inputs/bruker_1h/1')
data = ng.bruker.remove_digital_filter(dic, data)
'''

# Round 3 (LABKIT-011-PLAN.md): the model's own Phase 4 run 2 scripts, trimmed.
# s6_relaxation#1 run 2: curve_fit per residue, no labkit (S6 used labkit 0/15).
RELAX_RUN2 = '''import numpy as np
import pandas as pd
from scipy.optimize import curve_fit

def mono_exp(t, I0, R):
    return I0 * np.exp(-R * t)

r1 = pd.read_csv('inputs/R1_intensities.csv')
r1_cols = [c for c in r1.columns if c != 'residue']
t1 = np.array([float(c.replace('ms',''))/1000.0 for c in r1_cols])  # seconds
noe = pd.read_csv('inputs/hetNOE.csv')
R1_vals = np.full(len(r1), np.nan)
for i, row in r1.iterrows():
    popt, pcov = curve_fit(mono_exp, t1, row[r1_cols].to_numpy(float), p0=[row[r1_cols[0]], 1.5])
    R1_vals[i] = popt[1]
'''
# s6_hsqc_titration_s1#1 run 7: a hand global Kd with least_squares.
HSQC_RUN2 = '''import json, numpy as np
from scipy.optimize import least_squares
with open("csp_data.json") as fh:
    D = json.load(fh)
pts = D["pts"]                # ligand concentrations in uM
csp = D["csp"]
P_tot = 100.0
def frac_bound(Ltot, Kd):
    disc = (P_tot + Ltot + Kd)**2 - 4*P_tot*Ltot
    PL = 0.5 * (P_tot + Ltot + Kd - np.sqrt(disc))
    return PL / P_tot
res = least_squares(lambda p: resid(p), x0=[50.0])
'''
# fresh s1_standard_s6#3 run 3: linregress on the means, hand interpolation.
STANDARD_RUN2 = '''import pandas as pd
import numpy as np
from scipy import stats
df = pd.read_csv('inputs/bradford_6.csv')
std = df[df['sample'] == 'std'].copy()
unk = df[df['sample'].str.startswith('U')].copy()
std_mean = std.groupby('conc_ug_per_mL')['absorbance'].mean()
slope, intercept, r_value, p_value, std_err = stats.linregress(std_mean.index.values, std_mean.values)
for _, row in unk.iterrows():
    print(row['sample'], (row['absorbance'] - intercept) / slope)
'''
# A generic decay with a variable named t1: not NMR, gets the decay hint.
GENERIC_T1 = '''import numpy as np
from scipy.optimize import curve_fit
t1 = np.linspace(0, 10, 20)
popt, _ = curve_fit(lambda t, a, k, c: a * np.exp(-k * t) + c, t1, y)
R2 = 1 - ss_res / ss_tot  # R-squared
'''
# A line fit that is not a standard curve.
TREND = '''import numpy as np
slope, icpt = np.polyfit(years, temperature, 1)
print(slope)
'''

# Not lab fits: general optimisation, an arbitrary curve, plotting.
ROSENBROCK = '''from scipy.optimize import minimize
def rosen(x):
    return sum(100.0*(x[1:]-x[:-1]**2.0)**2.0 + (1-x[:-1])**2.0)
res = minimize(rosen, [1.3, 0.7, 0.8], method="nelder-mead")
'''
SINE_FIT = '''from scipy.optimize import curve_fit
import numpy as np
def wave(t, a, w, phi):
    return a * np.sin(w * t + phi)
popt, _ = curve_fit(wave, t, y, p0=[1, 2, 0])
'''
PLOT = '''import matplotlib.pyplot as plt
import numpy as np
x = np.linspace(0, 5)
plt.plot(x, np.exp(-x)); plt.yscale("log"); plt.savefig("decay.png")
'''
USES_LABKIT = '''import labkit
from scipy.optimize import curve_fit
r = labkit.fit("one_phase_decay", t, y)
'''


# Round 4 (LABKIT-013-PLAN.md): the model's own Phase 4 run 3 scripts, trimmed.
# s6_noesy_buildup#3: curve_fit with its own function named noe_buildup.
NOESY_RUN3 = '''import numpy as np
from scipy.optimize import curve_fit
mixing_times_ms = [100, 200, 300, 500]
mixing_times_s = [mt / 1000.0 for mt in mixing_times_ms]
cross_data = {mt: parse_cross(f'inputs/{mt}ms.txt') for mt in mixing_times_ms}
def noe_buildup(t, sigma, T):
    """A(t) = (A0/2) * (1 - exp(-2*sigma*t)) * exp(-t/T)"""
    return (A0_current / 2.0) * (1.0 - np.exp(-2.0 * sigma * t)) * np.exp(-t / T)
# cross-relaxation rate per drug peak / lipid group
popt, pcov = curve_fit(noe_buildup, t_data, y_data, p0=[0.01, 0.4])
'''
# s2_jasco_cd#3: read with labkit, MRE by hand (wrong units).
JASCO_RUN3 = '''import labkit
import numpy as np
data = labkit.read_jasco("inputs/cd_lysozyme.txt")
cd_222 = data.loc[data['wavelength_nm'] == 222.0, 'cd_mdeg'].values[0]
# Mean residue ellipticity calculation
c = 0.2 / 1000  # g/mL
l = 0.1  # cm
n = 129  # residues
mre_222 = (cd_222 * 100) / (c * l * n)
'''
# s6_hsqc_titration_s2#3: labkit for kd_fit, Sparky lists parsed by hand.
HSQC_RUN3 = '''import os, json, math
import labkit
def parse_list(fn):
    peaks = {}
    for line in open(fn):
        parts = line.split()
        assign = parts[0]
        if assign in ("Assignment", "w1", "w2", "Data", "Height"):
            continue
        w1 = float(parts[1])
        w2 = float(parts[2])
        peaks[assign.replace("N-H", "")] = (w2, w1)
    return peaks
res = labkit.nmr.kd_fit(conc, csp_dict, protein_conc=100.0)
'''
NOESY_LABKIT = '''from labkit import nmr
from scipy.optimize import curve_fit  # imported, not used
mixing = [0.1, 0.2, 0.3, 0.5]
r = nmr.noe_buildup(mixing, cross, diag0)
'''


def _check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' - ' + detail) if detail and not ok else ''}")
    return ok


def test_real_decay_script_gets_the_decay_hint():
    got = fit_hint(REAL_DECAY)
    return _check("the model's own lmfit decay gets the hint naming one_phase_decay",
                  got is not None and got[0] == "exponential" and "one_phase_decay" in got[1]
                  and "Y0 is the value at X=0" in got[1], repr(got))


def test_dose_response_and_mm_and_bruker():
    a, b, c, d = fit_hint(FOUR_PL), fit_hint(MM), fit_hint(BRUKER), fit_hint(RAW_FID)
    return _check("4PL, Michaelis-Menten, nmrglue Bruker and a raw FID read each get their hint",
                  a is not None and a[0] == "dose_response" and "x_scale" in a[1]
                  and b is not None and b[0] == "michaelis_menten"
                  and c is not None and c[0] == "bruker" and "labkit.nmr" in c[1]
                  and d is not None and d[0] == "bruker",
                  f"{a} {b} {c} {d}")


def test_general_code_gets_nothing():
    got = {n: fit_hint(c) for n, c in
           [("rosenbrock", ROSENBROCK), ("plot", PLOT), ("labkit", USES_LABKIT), ("trend", TREND)]}
    return _check("general optimisation, plotting, labkit code and a plain trend line get no hint",
                  all(v is None for v in got.values()), repr(got))


def test_custom_model_gets_the_general_fit_hint():
    got = fit_hint(SINE_FIT)
    return _check("a curve_fit of a model labkit does not name points at labkit.fit(f, ..., p0=)",
                  got is not None and got[0] == "custom_fit" and "p0=" in got[1]
                  and "several starting points" in got[1], repr(got))


def test_round4_domain_hints_on_run3_scripts():
    noe, cd, sp = fit_hint(NOESY_RUN3), fit_hint(JASCO_RUN3), fit_hint(HSQC_RUN3)
    rel, done = fit_hint(RELAX_RUN2), fit_hint(NOESY_LABKIT)
    ok = (noe is not None and noe[0] == "noe_buildup" and "nmr.noe_buildup" in noe[1]
          and cd is not None and cd[0] == "cd" and "mean_residue_ellipticity" in cd[1]
          and sp is not None and sp[0] == "sparky" and "read_sparky" in sp[1]
          and rel is not None and rel[0] == "relaxation"
          and done is None)
    return _check("run-3 NOESY (own noe_buildup function), JASCO and HSQC scripts get the NOE, CD and "
                  "Sparky hints even though two import labkit; hetNOE stays relaxation; a script "
                  "calling labkit's noe_buildup gets nothing",
                  ok, repr([noe and noe[0], cd and cd[0], sp and sp[0], rel and rel[0], done]))


def test_domain_hints_survive_labkit_use_general_ones_do_not():
    st = HintState()
    st.hint_for("c9", USES_LABKIT)
    general = st.hint_for("c9", SINE_FIT)
    domain = st.hint_for("c9", JASCO_RUN3)
    return _check("after the conversation used labkit, a CD hand calculation still gets its hint, "
                  "a custom curve_fit does not",
                  general is None and domain is not None and domain[0] == "cd", f"{general} {domain}")


def test_round3_nmr_and_standard_curve_hints():
    rel, kd, std = fit_hint(RELAX_RUN2), fit_hint(HSQC_RUN2), fit_hint(STANDARD_RUN2)
    gen, trend = fit_hint(GENERIC_T1), fit_hint(TREND)
    ok = (rel is not None and rel[0] == "relaxation" and "relaxation_fit" in rel[1]
          and kd is not None and kd[0] == "kd" and "kd_fit" in kd[1]
          and std is not None and std[0] == "standard_curve" and "labkit.interpolate" in std[1]
          and gen is not None and gen[0] == "exponential"
          and trend is None)
    return _check("run-2 relaxation, HSQC Kd and standard-curve scripts get labkit.nmr / interpolate "
                  "hints; a t1 variable or an R2 (R-squared) is not NMR; a plain trend line gets nothing",
                  ok, repr([rel and rel[0], kd and kd[0], std and std[0], gen and gen[0], trend]))


def test_dose_response_hint_explains_x_scale():
    got = fit_hint(FOUR_PL)
    return _check("the dose-response hint says x_scale describes x, 'linear' for concentrations",
                  got is not None and '"linear" for concentrations' in got[1], repr(got))

def test_once_per_conversation_and_silent_after_labkit():
    st = HintState()
    first = st.hint_for("c1", REAL_DECAY)
    again = st.hint_for("c1", REAL_DECAY)
    other_model = st.hint_for("c1", FOUR_PL)
    other_conv = st.hint_for("c2", REAL_DECAY)
    st.hint_for("c3", USES_LABKIT)
    after_labkit = st.hint_for("c3", FOUR_PL)
    return _check("each hint once per conversation; none once the conversation used labkit",
                  bool(first) and again is None and bool(other_model) and bool(other_conv)
                  and after_labkit is None,
                  f"{bool(first)} {again} {bool(other_model)} {bool(other_conv)} {after_labkit}")


def test_state_is_bounded():
    st = HintState(max_conversations=3)
    for i in range(10):
        st.hint_for(f"c{i}", PLOT)
    return _check("hint state keeps at most max_conversations entries", len(st._seen) == 3)


def test_upload_notes():
    bruker = upload_hint("bruker_1h.zip", b"PK\x03\x04", [
        "inputs/bruker_1h/1/acqus", "inputs/bruker_1h/1/fid", "inputs/bruker_1h/1/pdata/1/procs"])
    nano16 = upload_hint("nanodrop.txt",
                         "﻿Sample ID\tNucleic Acid(ng/uL)\tA260/A280\n".encode("utf-16"), ["inputs/nanodrop.txt"])
    jasco = upload_hint("cd.txt", b"TITLE\tx\nDATA TYPE\t\nYUNITS\tCD [mdeg]\nXYDATA\n200\t1\n", ["inputs/cd.txt"])
    gen5 = upload_hint("kin.txt", b"Software Version\t3.11\nReader Type:\tSynergy\n\nTime\tA1\n0:00:00\t0.1\n",
                       ["inputs/kin.txt"])
    pcrd = upload_hint("run.pcrd", b"\x00\x01binary", ["inputs/run.pcrd"])
    plain = upload_hint("data.csv", b"x,y\n1,2\n", ["inputs/data.csv"])
    image = upload_hint("gel.png", b"\x89PNG", ["inputs/gel.png"])
    topspin = upload_hint("100ms.txt", b"Object\tIntegral [abs]\t\xb4(F2) [ppm]\t\xb4(F1) [ppm]\t\n"
                          b"Integral 1\t225800.00\t8.6025\t0.8739\t\n", ["inputs/100ms.txt"])
    sparky = upload_hint("L0.list", b"      Assignment         w1         w2   Data Height\n\n"
                         b"           G2N-H    113.084      8.793        878767\n", ["inputs/L0.list"])
    sparky_zip = upload_hint("hsqc_titration.zip", b"PK\x03\x04",
                             ["inputs/titration/L0_0000uM.list", "inputs/titration/L1_0025uM.list"])
    ok = ("read_bruker('inputs/bruker_1h')" in bruker and "read_nanodrop" in nano16
          and "read_jasco" in jasco and "read_gen5" in gen5 and "export" in pcrd
          and plain == "" and image == "" and "read_integrals" in topspin
          and "read_sparky" in sparky and "read_sparky" in sparky_zip)
    return _check("uploads in labkit formats name the reader; a plain csv or an image gets nothing",
                  ok, repr([bruker, nano16, jasco, gen5, pcrd, plain, image, topspin, sparky, sparky_zip]))


def test_run_python_result_carries_the_hint_once():
    """End to end through run_python with the sandbox HTTP call stubbed."""
    import mcp.context as ctx
    from mcp.tools import sandbox

    class _Resp:
        status_code = 200
        text = ""

        def json(self):
            return {"stdout": "ok", "artifacts": []}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return _Resp()

    orig_client, orig_state = sandbox.httpx.AsyncClient, sandbox._labkit_hints
    sandbox.httpx.AsyncClient = _Client
    sandbox._labkit_hints = HintState()
    t1 = ctx.current_user_email.set("u@example.org")
    t2 = ctx.current_conversation_id.set("conv-lkh")
    try:
        r1 = asyncio.run(sandbox.run_python(REAL_DECAY))
        r2 = asyncio.run(sandbox.run_python(REAL_DECAY))
        r3 = asyncio.run(sandbox.run_python(ROSENBROCK))
    finally:
        sandbox.httpx.AsyncClient = orig_client
        sandbox._labkit_hints = orig_state
        ctx.current_user_email.reset(t1)
        ctx.current_conversation_id.reset(t2)
        sandbox.forget_source("conv-lkh")
    return _check("run_python adds labkit_hint to the first hand-rolled decay only",
                  "one_phase_decay" in r1.get("labkit_hint", "")
                  and "labkit_hint" not in r2 and "labkit_hint" not in r3,
                  f"{r1.get('labkit_hint')!r} {r2.get('labkit_hint')!r} {r3.get('labkit_hint')!r}")


# Run 4 (phase4d-labkit s2_jasco_cd#3, run 4): MRE by hand, then the HT
# limit interpolated between measured points. Trimmed to those two parts.
JASCO_RUN4 = """mre_222 = (cd_222 * mrw) / (10 * c_mg_ml * l_cm)

# Shortest wavelength where HT <= 600 V
ht = df['ht_voltage'].values
wl = df['wavelength'].values

idx = np.where((ht <= 600) & (np.roll(ht, -1) > 600))[0]
i = idx[0]
wl1, ht1 = wl[i], ht[i]
wl2, ht2 = wl[i+1], ht[i+1]
frac = (600 - ht1) / (ht2 - ht1)
min_wavelength_ht600 = wl1 + frac * (wl2 - wl1)

"""


def test_ht_cutoff_hint_after_the_cd_hint():
    st = HintState()
    first = st.hint_for("j4", JASCO_RUN4)
    second = st.hint_for("j4", JASCO_RUN4)
    third = st.hint_for("j4", JASCO_RUN4)
    covered = fit_hint("r = cd.ht_cutoff(wl, ht, max_v=600)\nok = ht <= 600")
    unrelated = fit_hint("x = df[df.hight > 600]")
    return _check("run-4 JASCO script: CD hint, then the ht_cutoff one (domain hints fall "
                  "through to the next); none once given, when ht_cutoff is called, or on 'hight'",
                  bool(first) and first[0] == "cd" and bool(second) and second[0] == "ht_cutoff"
                  and "ht_cutoff(" in second[1] and third is None and covered is None
                  and unrelated is None, f"{first and first[0]} {second and second[0]} {third} {covered} {unrelated}")


def test_a_given_model_hint_does_not_fall_through_to_custom_fit():
    st = HintState()
    decay = "from scipy.optimize import curve_fit\nf = lambda t, a, k: a*np.exp(-k*t)\ncurve_fit(f, t, y)\n"
    a, b = st.hint_for("g1", decay), st.hint_for("g1", decay)
    return _check("a given model hint does not fall through to custom_fit",
                  bool(a) and a[0] == "exponential" and b is None, f"{a and a[0]} {b}")


def test_competitive_hint_says_how_to_pass_x():
    h = fit_hint("from scipy.optimize import curve_fit\n"
                 "def competitive(X, vmax, km, ki): ...\ncurve_fit(competitive, (S, I), v)")
    return _check("the competitive hint says x=(S, I) and that one fit is the global fit",
                  bool(h) and h[0] == "competitive" and "x=(S, I)" in h[1] and "no fit_global" in h[1],
                  str(h))


EPR_READ_BY_HAND = """import numpy as np
dsc = open('inputs/sample.DSC').read()
params = dict(l.split(None, 1) for l in dsc.splitlines() if l and l[0].isalpha())
y = np.fromfile('inputs/sample.DTA', dtype='>f8')
nu = float(params['MWFQ']) / 1e9
"""
DEER_BY_HAND = """import numpy as np
from scipy.special import fresnel
t, V = np.loadtxt('deer.csv', delimiter=',', skiprows=1, unpack=True)
# DEER dipolar kernel
K = kernel(t, r)
P = np.linalg.lstsq(K, V, rcond=None)[0]
"""
DEERLAB = """import deerlab as dl
t, V = dl.deerload('deer.DTA')
"""
SPIN_BY_HAND = """import numpy as np
a = np.cumsum(y) * dx
di = np.trapz(a, x)   # double integral for the spin count vs TEMPOL
"""
G_BY_HAND = """h = 6.62607015e-34; mu_B = 9.2740100783e-24
g = h * 9.5e9 / (mu_B * B0 * 1e-3)
"""
EPR_WITH_LABKIT = """from labkit import epr
d = epr.load('inputs/sample.DSC')
y = np.fromfile('inputs/sample.DTA', dtype='>f8')
lf = epr.fit_line(d.field_mT, d.y, freq_GHz=d.freq_GHz)
"""


def test_epr_hints():
    st = HintState()
    got = {name: (fit_hint(code) or ("none",))[0] for name, code in (
        ("read", EPR_READ_BY_HAND), ("deer", DEER_BY_HAND), ("deerlab", DEERLAB), ("spin", SPIN_BY_HAND),
        ("g", G_BY_HAND))}
    covered = st.hint_for("e1", EPR_WITH_LABKIT)
    json_load = fit_hint("import json\nd = json.load(open('x.json'))\ny = np.fromfile('a.DTA', dtype='>f8')")
    up = upload_hint("sample.DSC", b"#DESC", ["sample.DSC"])
    up_zip = upload_hint("deer.zip", b"PK", ["deer/deer.DSC", "deer/deer.DTA"])
    return _check("EPR: hand-read BES3T, DEER by hand or via DeerLab, double integration, typed-in g "
                  "constants get labkit.epr hints; epr.load/fit_line calls are covered (json.load is not); "
                  "EPR uploads name epr.load",
                  got == {"read": "epr_read", "deer": "deer", "deerlab": "deer", "spin": "epr_integral", "g": "epr_g"}
                  and covered is None and json_load and json_load[0] == "epr_read"
                  and "labkit.epr.load" in up and "labkit.epr.load" in up_zip,
                  f"{got} {covered} {json_load and json_load[0]} {up[:40]!r}")


TESTS = [
    test_real_decay_script_gets_the_decay_hint,
    test_dose_response_and_mm_and_bruker,
    test_general_code_gets_nothing,
    test_custom_model_gets_the_general_fit_hint,
    test_round4_domain_hints_on_run3_scripts,
    test_domain_hints_survive_labkit_use_general_ones_do_not,
    test_round3_nmr_and_standard_curve_hints,
    test_dose_response_hint_explains_x_scale,
    test_once_per_conversation_and_silent_after_labkit,
    test_state_is_bounded,
    test_upload_notes,
    test_run_python_result_carries_the_hint_once,
    test_ht_cutoff_hint_after_the_cd_hint,
    test_a_given_model_hint_does_not_fall_through_to_custom_fit,
    test_competitive_hint_says_how_to_pass_x,
    test_epr_hints,
]


def main() -> int:
    passed = 0
    for t in TESTS:
        try:
            passed += bool(t())
        except Exception:
            traceback.print_exc()
            print(f"[FAIL] {t.__name__} raised")
    print(f"\n{passed}/{len(TESTS)} passed")
    return 0 if passed == len(TESTS) else 1




if __name__ == "__main__":
    sys.exit(main())
