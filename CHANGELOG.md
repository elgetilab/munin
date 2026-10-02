# Changelog

All notable changes to this project are recorded here. Versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]: 0.9.0

First public release, accompanying the paper. The plan behind it is
[`docs/RELEASE-PLAN.md`](docs/RELEASE-PLAN.md).

- Licensed under Apache-2.0 (`LICENSE`, `NOTICE`, `CITATION.cff`).
- Personal data removed from the tree and from history; the user whitelist and
  contributor list are now `.example` files, with the real ones kept on the
  hosts.
- LitQA2 content is no longer redistributed: answer scorecards keep verdicts
  and per-query arrays but not answer text, and the C2 question set keeps qids
  only. The LAB-Bench revision is pinned.
