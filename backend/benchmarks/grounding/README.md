# Grounding track

Synthetic cases (no user data, so committed) for SOURCE-LEDGER-PLAN.md: do
citations come from retrieved sources and match the DOI registry, across
turns, and do artifact links resolve? Mechanical checks only, no judge:
`citations_clean` (no [unverified] mark, citecheck finds nothing),
`dois_grounded` (every DOI in the answer is in a tool result or prompt),
`min_dois`, `no_phantom_artifact`, and no raw `[S7]` ids left.

Run with the user-failure runner against an instance, with the store pointed
at a private directory whose `cases` links here:

    G=/opt/munin/data/evals/user-failures/grounding
    mkdir -p $G && ln -sfn $PWD/grounding/cases $G/cases
    MUNIN_USERFAIL_DIR=$G python -m munin_bench.userfail.run run --tag <tag> --base http://127.0.0.1:8082
    MUNIN_USERFAIL_DIR=$G python -m munin_bench.userfail.run score --tag <tag>
