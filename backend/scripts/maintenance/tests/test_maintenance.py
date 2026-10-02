"""
munin-maintenance on/off against a temp dir (no root, no systemd, no cron).

Regressions from the 2026-09 review: `off` ran `systemctl enable --now
deepresearch-daemon`, reviving the retired MiroThinker daemon, and `on`
never placed the vLLM health hold, so munin-vllm-health.timer restarted
vLLM inside the serving window while maintenance was meant to keep the GPU
free. `off` must also never remove an operator's own hold.

Run:
    python backend/scripts/maintenance/tests/test_maintenance.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "maintenance.sh"
results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' - ' + detail) if detail and not ok else ''}")


def run(tmp: Path, *args: str) -> str:
    # A fake systemctl on PATH records any call, so a revived daemon shows up.
    env = {"PATH": f"{tmp}/bin:/usr/bin:/bin",
           "MUNIN_MAINTENANCE_FLAG": str(tmp / "maintenance.json"),
           "MUNIN_MAINTENANCE_CRON": str(tmp / "cron"),
           "MUNIN_VLLM_HOLD_FILE": str(tmp / "vllm-health.hold"),
           "MUNIN_MAINTENANCE_LOG": str(tmp / "log" / "maintenance.log")}
    out = subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True)
    return out.stdout + out.stderr


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="munin-maint-"))
    (tmp / "bin").mkdir()
    # Stubs first on PATH: `on` runs `vllm-service stop`, which must never
    # reach the real one and stop production vLLM.
    for name in ("systemctl", "vllm-service"):
        (tmp / "bin" / name).write_text(f"#!/bin/sh\necho \"$@\" >> {tmp}/{name}.calls\n")
        os.chmod(tmp / "bin" / name, 0o755)
    cron = "0 6 * * * root /opt/x/schedule-vllm.sh start\n0 2 * * * root /opt/x/schedule-vllm.sh stop\n"
    (tmp / "cron").write_text(cron)
    hold = tmp / "vllm-health.hold"

    run(tmp, "on", "testing")
    check("on writes the flag", (tmp / "maintenance.json").is_file())
    check("on disables the vLLM cron", all(l.startswith("#") for l in (tmp / "cron").read_text().splitlines()))
    check("on places the vLLM health hold", hold.is_file())
    check("on stops vLLM (stub)", (tmp / "vllm-service.calls").read_text().strip() == "stop")
    run(tmp, "off")
    check("off removes the flag", not (tmp / "maintenance.json").exists())
    check("off restores the cron", (tmp / "cron").read_text() == cron)
    check("off removes its own hold", not hold.exists())
    calls = (tmp / "systemctl.calls").read_text() if (tmp / "systemctl.calls").exists() else ""
    check("deepresearch-daemon never touched", "deepresearch" not in calls, calls)

    hold.write_text("operator: GPU experiment\n")
    run(tmp, "on")
    check("on leaves an operator's hold as it was", hold.read_text() == "operator: GPU experiment\n")
    out = run(tmp, "off")
    check("off keeps an operator's hold", hold.is_file(), out)

    passed = sum(results)
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
