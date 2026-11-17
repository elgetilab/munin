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
           [("rosenbrock", ROSENBROCK), ("sine", SINE_FIT), ("plot", PLOT), ("labkit", USES_LABKIT)]}
    return _check("optimisation, a non-labkit model, plotting and labkit code get no hint",
                  all(v is None for v in got.values()), repr(got))


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
    ok = ("read_bruker('inputs/bruker_1h')" in bruker and "read_nanodrop" in nano16
          and "read_jasco" in jasco and "read_gen5" in gen5 and "export" in pcrd
          and plain == "" and image == "")
    return _check("uploads in labkit formats name the reader; a plain csv or an image gets nothing",
                  ok, repr([bruker, nano16, jasco, gen5, pcrd, plain, image]))


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


TESTS = [
    test_real_decay_script_gets_the_decay_hint,
    test_dose_response_and_mm_and_bruker,
    test_general_code_gets_nothing,
    test_once_per_conversation_and_silent_after_labkit,
    test_state_is_bounded,
    test_upload_notes,
    test_run_python_result_carries_the_hint_once,
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
