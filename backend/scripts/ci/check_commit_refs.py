#!/usr/bin/env python3
"""
==============================================================================
Commit-reference checks
==============================================================================
Every benchmark scorecard records the commit it ran on (`git_sha`, and
`rescored_at_git_sha` when the statistics were recomputed later), and the
paper docs cite those commits as git `abc1234`. The history of this repository
was rewritten before the public release (2026-08-04, 2026-10-03, 2026-10-06),
which changes every hash; after the first two rewrites none of the cited
commits resolved any more and nothing noticed. This check fails as soon as a
cited commit is not in the history.

Checked:
  - `git_sha` and `rescored_at_git_sha` in every scorecard JSON, in
    backend/benchmarks/scorecards/ and docs/paper-kit/scorecards/;
  - "git `<hash>`" and "commit `<hash>`" in PAPER.md, README.md,
    backend/benchmarks/RESULTS.md and docs/paper-kit/.

Needs the full history: in CI, check out with fetch-depth: 0.

Run from the repo root:  python3 backend/scripts/ci/check_commit_refs.py
==============================================================================
"""
import glob
import re
import subprocess
import sys

SCORECARD_RE = re.compile(r'"(?:git_sha|rescored_at_git_sha)":\s*"([0-9a-f]{7,40})(?:-dirty)?"')
PROSE_RE = re.compile(r'\b(?:git|commit)\s+`([0-9a-f]{7,40})(?:-dirty)?`')


def is_commit(sha: str) -> bool:
    return subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"],
                          capture_output=True).returncode == 0


def main() -> int:
    shallow = subprocess.run(["git", "rev-parse", "--is-shallow-repository"],
                             capture_output=True, text=True).stdout.strip()
    if shallow == "true":
        print("FAIL: shallow clone; check out with fetch-depth: 0")
        return 1

    cited: dict[str, set[str]] = {}
    for path in (glob.glob("backend/benchmarks/scorecards/*.json")
                 + glob.glob("docs/paper-kit/scorecards/*.json")):
        for sha in SCORECARD_RE.findall(open(path).read()):
            cited.setdefault(sha, set()).add(path)
    for path in (["PAPER.md", "README.md", "backend/benchmarks/RESULTS.md"]
                 + glob.glob("docs/paper-kit/**/*.md", recursive=True)):
        for sha in PROSE_RE.findall(open(path).read()):
            cited.setdefault(sha, set()).add(path)

    missing = {sha: paths for sha, paths in cited.items() if not is_commit(sha)}
    for sha, paths in sorted(missing.items()):
        print(f"FAIL: {sha} is not a commit in this history; cited in "
              + ", ".join(sorted(paths)[:3]) + (" ..." if len(paths) > 3 else ""))
    print(f"{len(cited) - len(missing)}/{len(cited)} cited commits resolve")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
