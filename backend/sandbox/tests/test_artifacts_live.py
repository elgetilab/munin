"""
S0 plumbing checks for artifacts, figure descriptions, inputs and bundles,
against a running sandbox service (docs/agent-track/LAB-ANALYSIS-PLAN.md).

What must hold:
- every file user code writes (also in subfolders) becomes an artifact with a
  sha256, and its link serves exactly those bytes forever, even after the
  file is overwritten (the overwrite becomes a new version);
- unchanged files are not re-surfaced; links planted by user code never are;
- saved and inline matplotlib figures carry a description that matches what
  was drawn (scales, error bars, bar heights, size, dpi, layout problems);
- uploaded inputs are readable by user code but cannot be altered by it, and
  zip uploads are extracted safely;
- a bundle verifies against its own SHA256SUMS, holds every version, the
  inputs and the code, and its analysis.py reproduces the outputs.

Runs INSIDE a standalone container of the sandbox image, never against
production (same setup as test_isolation_live.py):

    docker run -d --name sbx-art --network none --cap-add SYS_ADMIN \\
        --pids-limit 2048 munin-sandbox:<tag>
    docker cp tests/test_artifacts_live.py sbx-art:/tmp/
    docker exec sbx-art python /tmp/test_artifacts_live.py
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import zipfile

import httpx

BASE = os.environ.get("SANDBOX_TEST_URL", "http://127.0.0.1:8090")
A = "aaaaaaaa-0000-4000-8000-0000000000a1"
C = "cccccccc-0000-4000-8000-0000000000c1"

c = httpx.Client(base_url=BASE, timeout=120)
results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(bool(ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' - ' + str(detail)) if detail and not ok else ''}")


def run(cid: str, code: str, timeout_s: float = 60) -> dict:
    r = c.post(f"/exec/{cid}", json={"code": code, "timeout_s": timeout_s})
    r.raise_for_status()
    return r.json()


def by_name(resp: dict, name: str) -> list[dict]:
    return [a for a in resp["artifacts"] if a["filename"] == name]


def fetch(cid: str, aid: str) -> httpx.Response:
    return c.get(f"/artifacts/{cid}/{aid}")


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def full_entry(cid: str, aid: str) -> dict:
    r = c.get(f"/artifacts/{cid}", params={"include_figures": True})
    return next(e for e in r.json()["artifacts"] if e["id"] == aid)


# ---- versioned, immutable artifacts ----------------------------------------

r1 = run(A, "open('result.csv','w').write('x,y\\n1,2\\n')")
v1 = by_name(r1, "result.csv")
check("new file surfaces with sha256", len(v1) == 1 and len(v1[0].get("sha256", "")) == 64, r1)
check("exec reports seq and code hash", r1.get("exec_seq") == 1 and len(r1.get("code_sha") or "") == 64, r1)
body1 = fetch(A, v1[0]["id"]).content
check("link serves the recorded bytes", sha(body1) == v1[0]["sha256"] and body1 == b"x,y\n1,2\n")

r2 = run(A, "open('result.csv','w').write('x,y\\n1,3\\n')")
v2 = by_name(r2, "result.csv")
check("overwrite becomes a new version",
      len(v2) == 1 and v2[0]["id"] != v1[0]["id"] and v2[0]["version"] == 2
      and v2[0]["previous_id"] == v1[0]["id"], r2)
check("old link still serves the old bytes", fetch(A, v1[0]["id"]).content == b"x,y\n1,2\n")
check("new link serves the new bytes", fetch(A, v2[0]["id"]).content == b"x,y\n1,3\n")

r3 = run(A, "import os; open('result.csv','w').write('x,y\\n1,3\\n'); os.utime('result.csv')")
check("rewriting identical bytes surfaces nothing", not by_name(r3, "result.csv"), r3)

r4 = run(A, "print('nothing written')")
check("untouched files are not re-surfaced", r4["artifacts"] == [], r4)

r5 = run(A, "import os; os.makedirs('out/sub', exist_ok=True); open('out/sub/table.tsv','w').write('a\\tb\\n')")
check("files in subfolders surface with their path", bool(by_name(r5, "out/sub/table.tsv")), r5)

r6 = run(A, "import os; os.symlink('/etc/passwd', 'passwd_link'); os.makedirs('.hidden', exist_ok=True); "
            "open('.hidden/x','w').write('1'); open('.dotfile','w').write('1')")
names6 = [a["filename"] for a in r6["artifacts"]]
check("links and hidden files are never surfaced", names6 == [], names6)

# ---- figure descriptions -----------------------------------------------------

FIG_CODE = r"""
import matplotlib.pyplot as plt, numpy as np
fig, ax = plt.subplots(figsize=(85/25.4, 60/25.4))
x = np.array([0.1, 1.0, 10.0])
ax.errorbar(x, [1.0, 2.0, 3.0], yerr=[0.1, 0.2, 0.3], fmt='o', color='#0072B2', label='WT')
ax.plot(np.logspace(-1, 1, 50), np.linspace(1, 3, 50), '-', label='fit')
ax.set_xscale('log'); ax.set_xlabel('[L] (uM)'); ax.set_ylabel('signal')
ax.text(1.0, 3.2, '**')
ax.legend()
fig.savefig('fit.png', dpi=300)
plt.close(fig)
"""
rf = run(A, FIG_CODE)
fa = by_name(rf, "fit.png")
check("saved figure surfaces", len(fa) == 1, rf)
if fa:
    fig = full_entry(A, fa[0]["id"]).get("figure") or {}
    ax = (fig.get("axes") or [{}])[0]
    eb = next((s for s in ax.get("series", []) if s.get("kind") == "errorbar"), {})
    check("figure: format, dpi and size in mm",
          fig.get("format") == "png" and fig.get("dpi") == 300
          and abs(fig["size_mm"][0] - 85) < 0.01, {k: fig.get(k) for k in ("format", "dpi", "size_mm")})
    check("figure: log x axis and labels",
          ax.get("xscale") == "log" and ax.get("xlabel") == "[L] (uM)", ax.get("xscale"))
    check("figure: error bar values recovered",
          eb.get("yerr") and all(abs(a - b) < 1e-9 for a, b in zip(eb["yerr"], [0.1, 0.2, 0.3]))
          and eb.get("y") == [1.0, 2.0, 3.0] and eb.get("color") == "#0072b2", eb)
    check("figure: fit line, legend and annotation",
          any(s.get("kind") == "line" and s.get("label") == "fit" for s in ax["series"])
          and sorted(ax.get("legend") or []) == ["WT", "fit"] and "**" in ax.get("texts", []), ax.get("legend"))
    check("summary returned with the artifact", "x log" in (fa[0].get("figure_summary") or ""),
          fa[0].get("figure_summary"))

BAR_CODE = r"""
import matplotlib.pyplot as plt
fig, ax = plt.subplots(figsize=(3, 2))
ax.bar(['ctrl', 'drug A', 'drug B'], [5.0, 7.5, 2.5], yerr=[0.5, 0.75, 0.25], color='#999999')
ax.set_ylabel('activity')
ax.set_title('A very long title that certainly does not fit into a figure this narrow at all')
plt.show()
"""
rb = run(A, BAR_CODE)
inline = [a for a in rb["artifacts"] if a.get("kind") == "inline"]
check("inline plot surfaces as an image artifact",
      len(inline) == 1 and inline[0]["content_type"] == "image/png", rb)
if inline:
    fig = full_entry(A, inline[0]["id"]).get("figure") or {}
    ax = (fig.get("axes") or [{}])[0]
    bars = next((s for s in ax.get("series", []) if s.get("kind") == "bar"), {})
    check("inline figure: bar heights and error bars",
          bars.get("values") == [5.0, 7.5, 2.5]
          and all(abs(a - b) < 1e-9 for a, b in zip(bars.get("yerr") or [], [0.5, 0.75, 0.25])), bars)
    check("inline figure: category labels", ax.get("xticklabels") == ["ctrl", "drug A", "drug B"],
          ax.get("xticklabels"))
    check("inline figure: clipped title detected",
          bool((fig.get("layout") or {}).get("clipped_text")), fig.get("layout"))

r_ns = run(A, "print([n for n in dir() if 'munin' in n or n in ('FIGURE_MIME', '_install')])")
check("hook leaves no names in the user namespace", r_ns["stdout"].strip() == "[]", r_ns["stdout"])

# ---- timeouts ---------------------------------------------------------------

rt = run(A, "open('partial.txt','w').write('half'); import time; time.sleep(30)", timeout_s=2)
check("timeout reported", rt["timed_out"], rt)
check("files written before a timeout still surface", bool(by_name(rt, "partial.txt")), rt)
check("kernel usable after timeout", run(A, "print(1+1)")["stdout"].strip() == "2")

# ---- inputs -------------------------------------------------------------------

csv_de = "Probe;Konz. (ng/µL);A260/A280\nS1;1200,45;1,92\nS2;85,10;1,71\n".encode("utf-8")
up = c.post(f"/inputs/{A}", params={"name": "nanodrop export.csv"}, content=csv_de)
check("input upload accepted", up.status_code == 200, up.text)
ri = run(A, "print(open('inputs/nanodrop export.csv', encoding='utf-8').read().splitlines()[1])")
check("kernel reads the input byte-identical", ri["stdout"].strip() == "S1;1200,45;1,92", ri)
rw = run(A, "open('inputs/nanodrop export.csv','w').write('tampered')")
check("kernel cannot overwrite an input", rw["error"] and "Permission" in rw["error"], rw)
rd = run(A, "import os; os.remove('inputs/nanodrop export.csv')")
check("kernel cannot delete an input", rd["error"] and "Permission" in rd["error"], rd)
run(A, "import os; os.rename('inputs', 'stolen'); os.makedirs('inputs'); "
       "open('inputs/nanodrop export.csv','w').write('fake')")
rr = run(A, "print(open('inputs/nanodrop export.csv', encoding='utf-8').read()[:5])")
check("renamed or replaced inputs are restored before the next run", rr["stdout"].strip() == "Probe", rr)
check("inputs are not surfaced as artifacts",
      not any(a["filename"].startswith("inputs/") for a in rr["artifacts"]), rr["artifacts"])

zbuf = io.BytesIO()
with zipfile.ZipFile(zbuf, "w") as zf:
    zf.writestr("exp1/acqus", "##$TD= 65536\n")
    zf.writestr("exp1/pdata/1/procs", "##$SI= 32768\n")
    zf.writestr("../evil.txt", "x")
    zf.writestr("/abs.txt", "x")
    zf.writestr("__MACOSX/._acqus", "x")
    link = zipfile.ZipInfo("exp1/link")
    link.external_attr = (0o120777 << 16)
    zf.writestr(link, "/etc/passwd")
uz = c.post(f"/inputs/{A}", params={"name": "bruker.zip"}, content=zbuf.getvalue())
paths = sorted(e["path"] for e in uz.json().get("added", [])) if uz.status_code == 200 else uz.text
check("zip extracted into a folder named after it",
      paths == ["bruker/abs.txt", "bruker/exp1/acqus", "bruker/exp1/pdata/1/procs"], paths)
rz = run(A, "import os; print(sorted(os.listdir('inputs/bruker/exp1')), os.path.exists('evil.txt'), "
            "os.path.exists('inputs/evil.txt'))")
check("zip members land under inputs/, no traversal or links",
      rz["stdout"].strip() == "['acqus', 'pdata'] False False", rz["stdout"])

big = c.post(f"/inputs/{A}", params={"name": "big.bin"}, content=b"\0" * (100 * 1024 * 1024 + 1))
check("uploads over 100 MB are refused", big.status_code == 413, big.status_code)

# ---- bundle -------------------------------------------------------------------

bz = c.get(f"/bundle/{A}")
check("bundle downloads", bz.status_code == 200 and bz.headers["content-type"] == "application/zip",
      bz.status_code)
zf = zipfile.ZipFile(io.BytesIO(bz.content))
sums = [ln.split("  ", 1) for ln in zf.read("SHA256SUMS").decode().splitlines()]
bad = [p for d, p in sums if sha(zf.read(p)) != d]
check("every file verifies against SHA256SUMS", sums and not bad, bad)
man = json.loads(zf.read("MANIFEST.json"))
outs = {o["path"] for o in man["outputs"]}
check("both versions of an overwritten file are in the bundle",
      {"outputs/result.v1.csv", "outputs/result.v2.csv"} <= outs, sorted(outs))
check("bundle bytes equal the artifact links",
      zf.read("outputs/result.v1.csv") == b"x,y\n1,2\n" and zf.read("outputs/result.v2.csv") == b"x,y\n1,3\n")
check("inputs, code, environment and figures included",
      "inputs/nanodrop export.csv" in zf.namelist() and "analysis.py" in zf.namelist()
      and any(n.startswith("code/run_") for n in zf.namelist())
      and "numpy==" in zf.read("environment.txt").decode()
      and any(n.startswith("figures/") for n in zf.namelist()), zf.namelist()[:20])
check("input in bundle is byte-identical", zf.read("inputs/nanodrop export.csv") == csv_de)

sel = c.get(f"/bundle/{A}", params={"ids": v1[0]["id"]})
selz = zipfile.ZipFile(io.BytesIO(sel.content))
check("selected bundle holds only the selection",
      [o["path"] for o in json.loads(selz.read("MANIFEST.json"))["outputs"]] == ["outputs/result.csv"])
check("unknown ids are refused", c.get(f"/bundle/{A}", params={"ids": "deadbeef"}).status_code == 404)

# ---- reproducibility (S5) -----------------------------------------------------

c.post(f"/inputs/{C}", params={"name": "conc.csv"},
       content=b"dose,resp\n0.1,0.05\n1,0.48\n10,0.93\n100,0.99\n")
run(C, "import pandas as pd, numpy as np\nd = pd.read_csv('inputs/conc.csv')\n")
run(C, "this is not python")  # a failed run must not break reproduction
run(C, "from scipy.optimize import curve_fit\n"
       "f = lambda x, ec50, h: x**h / (ec50**h + x**h)\n"
       "p, _ = curve_fit(f, d.dose, d.resp, p0=[1, 1])\n"
       "pd.DataFrame({'param': ['ec50', 'hill'], 'value': p}).to_csv('params.csv', index=False)\n"
       "import matplotlib.pyplot as plt\n"
       "plt.semilogx(d.dose, d.resp, 'o'); plt.savefig('curve.pdf'); plt.close()\n")
rz = c.get(f"/bundle/{C}")
with tempfile.TemporaryDirectory() as tmp:
    zipfile.ZipFile(io.BytesIO(rz.content)).extractall(tmp)
    proc = subprocess.run([sys.executable, "analysis.py"], cwd=tmp, capture_output=True, text=True,
                          timeout=120)
    check("analysis.py runs cleanly from the bundle", proc.returncode == 0, proc.stderr[-500:])
    same = (os.path.exists(os.path.join(tmp, "params.csv"))
            and open(os.path.join(tmp, "params.csv"), "rb").read()
            == open(os.path.join(tmp, "outputs", "params.csv"), "rb").read())
    check("analysis.py reproduces the outputs", same)

# ---- persistence across a kernel restart --------------------------------------

c.delete(f"/kernels/{A}")
check("artifact served after the kernel is gone", fetch(A, v1[0]["id"]).content == b"x,y\n1,2\n")
rk = run(A, "print('fresh kernel')")
check("a fresh kernel does not re-surface old files", rk["artifacts"] == [], rk["artifacts"])

passed = sum(results)
print(f"\n{passed}/{len(results)} passed")
sys.exit(0 if passed == len(results) else 1)
