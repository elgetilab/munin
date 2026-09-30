"""
End-to-end isolation checks against a running sandbox service.

Regression for 2026-09-30: firejail silently did nothing inside the
container, so kernels ran as root, read and wrote other conversations'
files, called the sandbox API on loopback and reached retrieval. Every
escape is exercised here with real kernels, alongside the features that
must keep working (plots, file artifacts, timeouts, reset, LaTeX).

Runs INSIDE a standalone container of the sandbox image, never against
production:

    docker run -d --name sbx-test --network none --cap-add SYS_ADMIN \
        --pids-limit 2048 munin-sandbox:<tag>
    docker cp tests/test_isolation_live.py sbx-test:/tmp/
    docker exec sbx-test python /tmp/test_isolation_live.py
"""

from __future__ import annotations

import json
import os
import sys
import time

import httpx

BASE = os.environ.get("SANDBOX_TEST_URL", "http://127.0.0.1:8090")
A = "aaaaaaaa-0000-4000-8000-00000000000a"
B = "bbbbbbbb-0000-4000-8000-00000000000b"

c = httpx.Client(base_url=BASE, timeout=120)
results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' - ' + detail) if detail and not ok else ''}")


def run(cid: str, code: str, timeout_s: float = 30) -> dict:
    r = c.post(f"/exec/{cid}", json={"code": code, "timeout_s": timeout_s})
    r.raise_for_status()
    return r.json()


def out(cid: str, code: str) -> str:
    d = run(cid, code)
    return (d["stdout"] + (d["error"] or "")).strip()


# --- the escapes -------------------------------------------------------------

d = run(A, "import os; print(os.getuid())")
uid_a = int(d["stdout"])
check("kernel runs as non-root", uid_a != 0, d["stdout"])
check("response reports isolated", d["isolated"] is True)
uid_b = int(out(B, "import os; print(os.getuid())"))
check("two conversations get different uids", uid_a != uid_b, f"{uid_a} {uid_b}")

check("cannot regain root", "PermissionError" in out(A, "import os; os.setuid(0)"))
check("cannot reach the sandbox API on loopback",
      "unreachable" in out(A, "import socket\ns=socket.socket();s.settimeout(2)\n"
                              "try:\n s.connect(('127.0.0.1',8090));print('REACHABLE')\n"
                              "except OSError:\n print('unreachable')"))
check("cannot list /scratch", "PermissionError" in out(A, "import os; os.listdir('/scratch')"))
out(B, "open('secret.txt','w').write('b-only')")
check("cannot read another conversation's file",
      "PermissionError" in out(A, f"print(open('/scratch/{B}/secret.txt').read())"))
check("cannot write the service's code",
      "PermissionError" in out(A, "open('/app/app/main.py','a').write('#')"))
check("cannot read the artifact manifests",
      "PermissionError" in out(A, "import os; os.listdir('/scratch-meta')"))
check("capabilities all dropped",
      "CapEff:\t0000000000000000" in out(A, "print(open('/proc/self/status').read())"))

# symlink tricks against the root service
d = run(A, "import os; os.symlink('/etc/shadow', 'leak.txt'); print('ok')")
check("a planted symlink is not surfaced as an artifact",
      not any(a["filename"] == "leak.txt" for a in d["artifacts"]), json.dumps(d["artifacts"]))
d = run(A, "open('data.csv','w').write('a,b\\n1,2\\n')")
csv = next((a for a in d["artifacts"] if a["filename"] == "data.csv"), None)
check("a regular file becomes an artifact", csv is not None, json.dumps(d["artifacts"]))
r = c.get(f"/artifacts/{A}/{csv['id']}")
check("artifact is served", r.status_code == 200 and r.content == b"a,b\n1,2\n", str(r.status_code))
check("artifact keeps its download filename",
      r.headers.get("content-disposition") == 'attachment; filename="data.csv"',
      r.headers.get("content-disposition", ""))
run(A, "import os; os.remove('data.csv'); os.symlink('/etc/shadow', 'data.csv')")
r = c.get(f"/artifacts/{A}/{csv['id']}")
check("artifact swapped for a symlink is refused", r.status_code == 404, f"{r.status_code} {r.text[:80]}")

check("bad conversation id refused", c.post("/exec/..", json={"code": "1"}).status_code in (400, 404))

# leftover background processes die with the kernel
run(A, "import subprocess; subprocess.Popen(['sleep','600'], start_new_session=True)")
c.delete(f"/kernels/{A}")
time.sleep(1)
alive = [p for p in os.listdir("/proc") if p.isdigit()
         and f"\t{uid_a}\t" in open(f"/proc/{p}/status").read()]
check("no process of the uid survives kernel shutdown", not alive, str(alive))

# --- what must keep working --------------------------------------------------

d = run(A, "import numpy as np, pandas as pd\nprint(pd.DataFrame({'x': np.arange(3)}).x.sum())")
check("numpy + pandas", d["stdout"].strip() == "3", d["stdout"] + (d["error"] or ""))
d = run(A, "import matplotlib.pyplot as plt\nplt.plot([1,2,3]); plt.show()")
png = [a for a in d["artifacts"] if a["content_type"] == "image/png"]
check("inline plot captured", len(png) == 1, json.dumps(d["artifacts"]) + (d["error"] or ""))
if png:
    r = c.get(f"/artifacts/{A}/{png[0]['id']}")
    check("plot served", r.status_code == 200 and r.content[:4] == b"\x89PNG", str(r.status_code))
run(A, "kept = 41")
d = run(A, "import time; time.sleep(30)", timeout_s=2)
check("timeout interrupts", d["timed_out"] is True)
check("state survives the interrupt", out(A, "print(kept + 1)") == "42")
out(A, "open('keep.txt','w').write('x')")
c.post(f"/reset/{A}")
check("reset wipes state", "NameError" in out(A, "print(kept)"))
check("files survive reset", out(A, "import os; print(os.path.exists('keep.txt'))") == "True")
d = run(A, "import sklearn, scipy, sympy; print('ok')")
check("scientific stack imports", d["stdout"].strip() == "ok", d["error"] or "")

tex = r"""\documentclass{article}\begin{document}Hello \cite{k}.\bibliographystyle{plain}\bibliography{refs}\end{document}"""
bib = "@article{k, title={T}, author={A. B.}, journal={J}, year={2020}}"
r = c.post(f"/latex/{A}", json={"source": tex, "bibliography": bib})
d = r.json()
check("latex with bibliography compiles", d.get("success") is True,
      (d.get("error_message") or "") + str(d.get("errors"))[:200])
if d.get("pdf_artifact"):
    r = c.get(f"/artifacts/{A}/{d['pdf_artifact']['id']}")
    check("pdf served", r.status_code == 200 and r.content[:5] == b"%PDF-", str(r.status_code))
out(B, "open('secret.txt','w').write('b-only-latex-canary')")
r = c.post(f"/latex/{A}", json={"source": r"\documentclass{article}\begin{document}\input{/scratch/" + B + r"/secret.txt}\end{document}"})
check("latex cannot read another conversation's file",
      "b-only-latex-canary" not in json.dumps(r.json()) and r.json().get("success") is False)

c.delete(f"/kernels/{A}")
c.delete(f"/kernels/{B}")
passed = sum(results)
print(f"\n{passed}/{len(results)} passed")
sys.exit(0 if passed == len(results) else 1)
