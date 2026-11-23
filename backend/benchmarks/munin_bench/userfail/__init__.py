"""User-failure regression track (Track UF).

A private test set mined from real conversations where Munin failed a user
(reported chats, corrections, re-asks, error markers). Each case replays the
user's turns against the live chat API, applies mechanical checks, and has a
pinned Claude model grade the final answer against a verified rubric.

The cases are user text, so they never enter git: they live in
``/opt/munin/data/evals/user-failures/`` (``MUNIN_USERFAIL_DIR``), created
by ``deploy.sh`` with mode 700. Only this code and scorecards holding case
ids and pass/fail land in the repository.

Pipeline (each step resumable):
    run    replay cases, mechanical checks  -> <store>/runs/<tag>/records.jsonl
    judge  Claude judge via the Batch API   -> <store>/runs/<tag>/judged.jsonl
    score  scorecard (ids + verdicts only)  -> scorecards/userfail/<date>_<tag>.json
    compare two scorecards, paired          -> fixed / regressed / McNemar p

Every scorecard stamps the harness (git sha, retrieval image) and the served
model (from /api/system), so a pair of runs that changes only one of them
attributes the difference: same model + new harness = harness effect, same
harness + new model = model effect.
"""
