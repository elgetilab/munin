"""Tests for the retyping hint (retype_check.py, LABKIT-018-PLAN.md change 1).

Why: in the full-suite lab runs the model copied the rows it had been shown
(the upload preview, or head(10)) into an array literal and invented the
rest; s1_nist_thurber#3 and s1_nist_misra1d#2 failed that way. Uploads now
keep their numeric columns, and a run whose literal matches a column for 5
or more positions and then departs gets a `data_hint`. Faithful copies,
start values, grids and values typed from the prompt must stay silent.

Run standalone or under pytest:
    python backend/retrieval/tests/test_retype_check.py
"""
from __future__ import annotations

import asyncio
import io
import sys
import traceback
import zipfile
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import retype_check as rc  # noqa: E402

THURBER = (
    "x,y\n-3.067E0,80.574E0\n-2.981E0,84.248E0\n-2.921E0,87.264E0\n-2.912E0,87.195E0\n"
    "-2.840E0,89.076E0\n-2.797E0,89.608E0\n-2.702E0,89.868E0\n-2.699E0,90.101E0\n-2.633E0,92.405E0\n"
    "-2.481E0,95.854E0\n-2.363E0,100.696E0\n-2.322E0,101.060E0\n-1.501E0,401.672E0\n-1.460E0,390.724E0\n"
    "-1.274E0,567.534E0\n-1.212E0,635.316E0\n-1.100E0,733.054E0\n-1.046E0,759.087E0\n-0.915E0,894.206E0\n"
    "-0.714E0,990.785E0\n-0.566E0,1090.109E0\n-0.545E0,1080.914E0\n-0.400E0,1122.643E0\n-0.309E0,1178.351E0\n"
    "-0.109E0,1260.531E0\n-0.103E0,1273.514E0\n0.010E0,1288.339E0\n0.119E0,1327.543E0\n0.377E0,1353.863E0\n"
    "0.790E0,1414.509E0\n0.963E0,1425.208E0\n1.006E0,1421.384E0\n1.115E0,1442.962E0\n1.572E0,1464.350E0\n"
    "1.841E0,1468.705E0\n2.047E0,1447.894E0\n2.200E0,1457.628E0\n")
MISRA1D = (
    "x,y\n77.6E0,10.07E0\n114.9E0,14.73E0\n141.1E0,17.94E0\n190.8E0,23.93E0\n239.9E0,29.61E0\n"
    "289.0E0,35.18E0\n332.8E0,40.02E0\n378.4E0,44.82E0\n434.8E0,50.76E0\n477.3E0,55.05E0\n"
    "536.8E0,61.01E0\n593.1E0,66.40E0\n689.1E0,75.47E0\n760.0E0,81.78E0\n")

# full-lk17 s1_nist_thurber#3, run 2 (trimmed): rows 1-10 copied from head(10), the rest invented.
THURBER_RUN2 = """import numpy as np
from scipy.optimize import least_squares

# Data
x = np.array([-3.067, -2.981, -2.921, -2.912, -2.840, -2.797, -2.702, -2.699, -2.633, -2.481,
              -2.442, -2.410, -2.388, -2.354, -2.340, -2.298, -2.251, -2.202, -2.170, -2.142,
              -2.085, -2.017, -1.970, -1.921, -1.861, -1.780, -1.713, -1.639, -1.537, -1.442,
              -1.370, -1.266, -1.172, -1.075, -0.980, -0.880, -0.772])
y = np.array([80.574, 84.248, 87.264, 87.195, 89.076, 89.608, 89.868, 90.101, 92.405, 95.854,
              97.628, 99.664, 101.609, 103.558, 105.415, 107.358, 109.286, 111.202, 113.109, 115.000,
              116.876, 118.732, 120.564, 122.369, 124.143, 125.883, 127.587, 129.252, 130.875, 132.456,
              133.992, 135.481, 136.919, 138.301, 139.621, 140.874, 142.064])
b0 = [1000.0, 1000.0, 400.0, 40.0, 0.7, 0.3, 0.03]
"""
# full-lk17 s1_nist_misra1d#2, run 2: rows 1-7 copied, then a made-up even grid.
MISRA1D_RUN2 = """import numpy as np
x = np.array([77.6, 114.9, 141.1, 190.8, 239.9, 289.0, 332.8, 380.0, 429.0, 478.0, 527.0, 576.0, 625.0, 760.0])
y = np.array([10.07, 14.73, 17.94, 23.93, 29.61, 35.18, 40.02, 45.00, 50.00, 55.00, 60.00, 65.00, 70.00, 81.78])
b0 = [500.0, 0.0001]
"""
FAITHFUL = """x = np.array([77.6, 114.9, 141.1, 190.8, 239.9, 289.0, 332.8, 378.4, 434.8, 477.3, 536.8, 593.1,
              689.1, 760.0])
head = [80.574, 84.248, 87.264, 87.195, 89.076, 89.608, 89.868, 90.101, 92.405, 95.854]
"""
QUIET = """p0 = [1000.0, 1000.0, 400.0, 40.0, 0.7, 0.3, 0.03, 1.0, 1.0, 1.0, 1.0]
grid = np.array([0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2])
conc_from_prompt = [0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500]
short = [77.6, 114.9, 141.1, 190.8, 239.9]
"""


def _check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' - ' + detail) if detail and not ok else ''}")
    return ok


def _state() -> rc.RetypeState:
    st = rc.RetypeState()
    st.remember("c", "thurber.csv", THURBER.encode(), ["inputs/thurber.csv"])
    st.remember("c", "misra1d.csv", MISRA1D.encode(), ["inputs/misra1d.csv"])
    return st


def test_columns_of_a_text_table() -> bool:
    cols = rc.file_columns("thurber.csv", THURBER.encode(), ["inputs/thurber.csv"])
    bare = rc.parse_columns("1 2\n3 4\n5 6\n")
    binary = rc.file_columns("x.DTA", b"\x00\x01" * 100, ["inputs/x.DTA"])
    big = rc.file_columns("big.csv", b"1,2\n" * (rc.MAX_FILE_BYTES // 4 + 1), ["inputs/big.csv"])
    return _check("columns come with their header names; binary and oversized files give none",
                  [c.name for c in cols] == ["x", "y"] and len(cols[1].values) == 37
                  and cols[1].values[36] == 1457.628 and [n for n, _ in bare] == ["column 1", "column 2"]
                  and binary == [] and big == [], f"{[(c.name, len(c.values)) for c in cols]} {bare}")


def test_thurber_and_misra1d_retyping_fires() -> bool:
    st = _state()
    th = st.hint_for("c", THURBER_RUN2)
    mi = st.hint_for("c", MISRA1D_RUN2)
    ok = (th is not None and "matches rows 1-10 of `x` in thurber.csv and then differs; the file has 37 rows" in th
          and "rows 1-10 of `y` in thurber.csv" in th and "'inputs/thurber.csv'" in th
          and mi is not None and "rows 1-7 of `x` in misra1d.csv" in mi and "the file has 14 rows" in mi
          and "Read the file instead of typing values" in mi)
    return _check("the thurber#3 and misra1d#2 literals get the hint naming rows, column, file, length",
                  ok, f"{th!r} {mi!r}")


def test_faithful_copies_start_values_grids_and_prompt_values_stay_silent() -> bool:
    st = _state()
    return _check("a faithful copy, p0, a grid, prompt values and short literals get nothing",
                  st.hint_for("c", FAITHFUL) is None and st.hint_for("c", QUIET) is None)


def test_once_per_column_per_turn_and_no_state_no_hint() -> bool:
    st = _state()
    first = st.hint_for("c", THURBER_RUN2)
    again = st.hint_for("c", THURBER_RUN2)
    st.new_turn("c")
    next_turn = st.hint_for("c", THURBER_RUN2)
    restarted = rc.RetypeState().hint_for("c", THURBER_RUN2)
    other = st.hint_for("other-conv", THURBER_RUN2)
    return _check("fires once per file and column per turn, again in the next turn; nothing without "
                  "the upload's columns (another conversation, after a restart)",
                  bool(first) and again is None and bool(next_turn) and restarted is None and other is None)


def test_zip_members_and_bounds() -> bool:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("set/thurber.csv", THURBER)
        zf.writestr("set/fid", b"\x00\x01\x02" * 50)
    st = rc.RetypeState(max_conversations=2)
    n = st.remember("z", "set.zip", buf.getvalue(), ["inputs/set/set/thurber.csv", "inputs/set/set/fid"])
    hint = st.hint_for("z", THURBER_RUN2)
    for i in range(3):
        st.remember(f"o{i}", "misra1d.csv", MISRA1D.encode(), ["inputs/misra1d.csv"])
    evicted = st.hint_for("z", THURBER_RUN2)
    return _check("a .zip's text members are kept under their sandbox path; the state is bounded",
                  n == 2 and hint and "'inputs/set/set/thurber.csv'" in hint and evicted is None,
                  f"{n} {hint!r}")


def test_run_python_and_upload_carry_it() -> bool:
    """The upload path keeps the columns and run_python adds data_hint."""
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

    orig_client, orig_state = sandbox.httpx.AsyncClient, sandbox._retype
    sandbox.httpx.AsyncClient = _Client
    sandbox._retype = rc.RetypeState()
    t1 = ctx.current_user_email.set("u@example.org")
    t2 = ctx.current_conversation_id.set("conv-retype")
    try:
        sandbox.remember_input("conv-retype", "thurber.csv", THURBER.encode(), ["inputs/thurber.csv"])
        sandbox.remember_input("conv-retype", "broken.csv", None, [])      # never raises
        r1 = asyncio.run(sandbox.run_python(THURBER_RUN2))
        r2 = asyncio.run(sandbox.run_python(THURBER_RUN2 + "\nprint(1)\n"))
        sandbox.new_turn("conv-retype")
        r3 = asyncio.run(sandbox.run_python(THURBER_RUN2 + "\nprint(2)\n"))
    finally:
        sandbox.httpx.AsyncClient = orig_client
        sandbox._retype = orig_state
        ctx.current_user_email.reset(t1)
        ctx.current_conversation_id.reset(t2)
        sandbox.forget_source("conv-retype")
    return _check("run_python adds data_hint once per turn after an upload",
                  "thurber.csv" in r1.get("data_hint", "") and "data_hint" not in r2
                  and "data_hint" in r3, f"{r1.get('data_hint')!r} {r2.get('data_hint')!r}")


TESTS = [
    test_columns_of_a_text_table,
    test_thurber_and_misra1d_retyping_fires,
    test_faithful_copies_start_values_grids_and_prompt_values_stay_silent,
    test_once_per_column_per_turn_and_no_state_no_hint,
    test_zip_members_and_bounds,
    test_run_python_and_upload_carry_it,
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
