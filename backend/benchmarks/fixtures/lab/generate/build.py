#!/usr/bin/env python3
"""Regenerate every public lab-analysis fixture.

    cd backend/benchmarks
    python fixtures/lab/generate/build.py            # rewrite fixtures/lab/cases/
    python fixtures/lab/generate/build.py --check    # regenerate to a temp dir, compare SHA256SUMS

Held-out variants (plan D5) use the same generators with private seeds:

    python fixtures/lab/generate/build.py --out /opt/munin/data/evals/lab --seeds <private list>
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import nist  # noqa: E402
import s1  # noqa: E402
import s2  # noqa: E402
import s3s5  # noqa: E402
import s6  # noqa: E402

LAB = HERE.parent
CASES = LAB / "cases"
SUMS = LAB / "SHA256SUMS"


def build_all(out: Path, seeds=None) -> None:
    if seeds:
        s1.build(out, seeds=seeds)        # only the seeded families vary
        return
    s1.build(out)
    nist.build(out)
    s2.build(out)
    s3s5.build(out)
    s6.build(out)


def sums(root: Path) -> str:
    lines = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            lines.append(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(root).as_posix()}")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=CASES)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--seeds", type=int, nargs="*")
    args = ap.parse_args()
    if args.check:
        with tempfile.TemporaryDirectory() as tmp:
            build_all(Path(tmp))
            fresh = sums(Path(tmp))
        stored = SUMS.read_text() if SUMS.exists() else ""
        if fresh != stored:
            a, b = set(stored.splitlines()), set(fresh.splitlines())
            for line in sorted(a ^ b)[:20]:
                print(("- " if line in a else "+ ") + line)
            print("fixtures differ from SHA256SUMS")
            return 1
        print(f"{len(fresh.splitlines())} fixture files match SHA256SUMS")
        return 0
    if args.out == CASES and CASES.exists():
        shutil.rmtree(CASES)
    args.out.mkdir(parents=True, exist_ok=True)
    build_all(args.out, args.seeds)
    if args.out == CASES:
        SUMS.write_text(sums(CASES))
    n = len(list(args.out.rglob("case.json")))
    print(f"{n} cases written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
