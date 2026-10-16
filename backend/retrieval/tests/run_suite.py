#!/usr/bin/env python3
"""
Run one tier of backend/retrieval/tests, as classified in suite.txt.

    python tests/run_suite.py --tier hermetic      # what GitHub CI runs
    python tests/run_suite.py --tier live          # nightly on hugin
    python tests/run_suite.py --check              # manifest only

Each file runs in its own interpreter (they are standalone scripts that print
[PASS]/[FAIL] and exit non-zero on failure, see README.md), with its own
throwaway CHATS_DB_PATH. That override is unconditional: a test that imports
chat_store without one opens /data/chats.db, which is the live chat database
in the deployed container.

One COMPLETE line per file, name and result together, flushed as it lands, so
a partial log reads as partial rather than as a hang (README.md, "Read a
truncated run as truncated").

Exit: 0 every selected file passed, 1 something failed or timed out (or the
budget ran out before it started, or, in the live tier, SKIPped), 2 the
manifest and the directory disagree.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# NOT resolve(): in a layout where /app/tests is a symlink, the tests must
# still be invoked as /app/tests/<file> so their own /app imports line up.
HERE = Path(os.path.abspath(__file__)).parent
ROOT = HERE.parent
MANIFEST = HERE / "suite.txt"
TIERS = {"hermetic", "live", "manual"}
RUNNERS = {"", "pytest"}


def load_manifest(path: Path = MANIFEST) -> tuple[dict, list[str]]:
    """{file: (tiers, runner)} plus a list of problems with the manifest."""
    entries: dict = {}
    problems: list[str] = []
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        cols = line.split()
        if len(cols) not in (2, 3):
            problems.append(f"suite.txt:{n}: expected 'file tiers [runner]', got {raw!r}")
            continue
        name, tiers = cols[0], set(cols[1].split(","))
        runner = cols[2] if len(cols) == 3 else ""
        if tiers - TIERS:
            problems.append(f"suite.txt:{n}: unknown tier(s) {sorted(tiers - TIERS)}")
        if runner not in RUNNERS:
            problems.append(f"suite.txt:{n}: unknown runner {runner!r}")
        if name in entries:
            problems.append(f"suite.txt:{n}: {name} listed twice")
        entries[name] = (tiers, runner)
    on_disk = {p.name for p in HERE.glob("test_*.py")} | {p.name for p in HERE.glob("smoke_*.py")}
    for name in sorted(on_disk - set(entries)):
        problems.append(f"{name} is not classified in suite.txt (add it as hermetic, live or manual)")
    for name in sorted(set(entries) - on_disk):
        problems.append(f"suite.txt lists {name}, which does not exist")
    return entries, problems


def _command(name: str, runner: str) -> list[str]:
    path = str(HERE / name)
    if runner == "pytest" and importlib.util.find_spec("pytest") is not None:
        return [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", path]
    return [sys.executable, path]


def _skips(output: str) -> int:
    return sum(1 for ln in output.splitlines()
               if ln.lstrip().startswith(("[SKIP]", "SKIP ")))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tier", choices=sorted(TIERS))
    ap.add_argument("--check", action="store_true", help="validate suite.txt and exit")
    ap.add_argument("--list", action="store_true", help="print the selected files and exit")
    ap.add_argument("--only", nargs="*", default=None, help="restrict to these files")
    ap.add_argument("--timeout", type=int, default=300, help="per-file limit, seconds")
    ap.add_argument("--budget", type=int, default=0,
                    help="wall-clock limit for the whole run, seconds (0 = none); "
                         "files not started by then count as failures")
    ap.add_argument("--log-dir", default="", help="keep each file's full output here")
    args = ap.parse_args(argv)

    entries, problems = load_manifest()
    if problems:
        for p in problems:
            print(f"[MANIFEST] {p}")
        return 2
    if args.check:
        print(f"[MANIFEST] ok: {len(entries)} files classified")
        return 0
    if not args.tier:
        ap.error("--tier is required unless --check is given")

    selected = sorted(n for n, (tiers, _r) in entries.items() if args.tier in tiers)
    if args.only:
        wanted = {o if o.endswith(".py") else o + ".py" for o in args.only}
        unknown = wanted - set(entries)
        if unknown:
            print(f"[MANIFEST] not in suite.txt: {sorted(unknown)}")
            return 2
        selected = [n for n in selected if n in wanted]
    if args.list:
        print("\n".join(selected))
        return 0

    log_dir = Path(args.log_dir) if args.log_dir else None
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)

    print(f"tier={args.tier} files={len(selected)} python={sys.version.split()[0]} root={ROOT}",
          flush=True)
    start = time.monotonic()
    failed: list[str] = []
    for name in selected:
        stem = name[:-3]
        if args.budget and time.monotonic() - start > args.budget:
            print(f"{stem:<44} NOT RUN (budget of {args.budget}s spent)", flush=True)
            failed.append(name)
            continue
        db_dir = tempfile.mkdtemp(prefix=f"munin-suite-{stem}-")
        env = dict(os.environ)
        env["CHATS_DB_PATH"] = os.path.join(db_dir, "chats.db")
        env["MUNIN_TEST_TIER"] = args.tier
        if args.tier == "live":
            env["MUNIN_TEST_LIVE"] = "1"
        else:
            env.pop("MUNIN_TEST_LIVE", None)
        t0 = time.monotonic()
        try:
            proc = subprocess.run(_command(name, entries[name][1]), cwd=str(ROOT), env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, timeout=args.timeout)
            rc, out = proc.returncode, proc.stdout
        except subprocess.TimeoutExpired as e:
            rc = "timeout"
            out = (e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) \
                else (e.stdout or "")
        secs = time.monotonic() - t0
        skips = _skips(out)
        ok = rc == 0
        note = ""
        # A live check SKIPs when its services are unreachable, which is the
        # right call in CI and a silent green here: in the live tier the
        # services are the point, so a skip is a failure.
        if ok and skips and args.tier == "live":
            ok, note = False, "  (SKIP in the live tier: a service it needs was not reachable)"
        print(f"{stem:<44} {'PASS' if ok else 'FAIL'} rc={rc} {secs:5.1f}s skips={skips}{note}",
              flush=True)
        if log_dir:
            (log_dir / f"{stem}.log").write_text(out, encoding="utf-8")
        if not ok:
            failed.append(name)
            for ln in out.splitlines()[-40:]:
                print(f"    | {ln}")
            sys.stdout.flush()

    total = time.monotonic() - start
    print(f"\n{len(selected) - len(failed)}/{len(selected)} files passed "
          f"(tier={args.tier}, {total:.0f}s)")
    for name in failed:
        print(f"  failed: {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
