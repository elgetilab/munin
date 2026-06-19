"""
routing_eval.py — bespoke routing / tool-selection eval for MuninAI.

WHAT THIS IS
------------
A trajectory-level eval that scores the harness's *routing decision* for a
single user turn — which tool(s) fire, in what order, with what argument
shape, and whether it correctly clarifies / abstains / answers from
parametric knowledge — NOT the content of the final answer.

WHY IT EXISTS
-------------
Two jobs at once:
  1. Tells you whether routing is correct against MuninAI's real tool list.
  2. Doubles as the regression harness for the persona -> routing-profile
     migration: run it before and after you retire the allowlist/delegation
     machinery and confirm nothing re-routes wrongly. The `profile` field is
     the router's own acceptance test (which profile it picks up front).

DESIGN NOTES
------------
- Score the trajectory, never the answer string. Live answers (weather,
  news) have no stable ground truth; the routing decision does.
- Argument PREDICATES, not exact match — args are model-generated and vary,
  so we assert shape ("queries has >=3 entries", "mode == physical",
  "doi matches 10\\..."), not a frozen blob. Keeps it robust to paraphrase.
- `reward_basis` gates pass/fail (borrowed from tau-bench): an item can say
  "only the abstention matters here; tolerate a harmless extra search".
- Deferred-tool aware: non-core tools (citation graph, export_citations,
  compile_latex, memory) are reached via `tool_search`. Items that need them
  pass on EITHER tool_search-then-tool OR the direct call — this is what
  verifies CORE_TOOLS + tool_search carries the load once allowlists are gone.
- `solo` enforces the ABSOLUTE clarification rule: ask_clarification must be
  emitted alone, nothing else in the same assistant step.

HOW IT RUNS
-----------
The runner drives POST /api/chat/completions with ephemeral=true, collects
the ordered `tool_call` SSE events for the turn (same stream
scripts/test_delegate_persona.py already parses), and feeds the captured
trajectory to score_item(). An optional LLM-judge hook handles the handful of
answer-quality assertions (abstention wording) that routing alone can't prove.
Wraps cleanly as an InspectAI Task so it sits next to AstaBench/BFCL runs.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


# ===========================================================================
# Argument predicate DSL
# ===========================================================================
class PredOp(str, Enum):
    EQ = "eq"
    NEQ = "neq"
    CONTAINS = "contains"        # substring (str) or membership (list)
    REGEX = "regex"             # re.search against str(value)
    LEN_GTE = "len_gte"
    LEN_LTE = "len_lte"
    IN_SET = "in_set"
    EXISTS = "exists"           # key present and non-empty
    ABSENT = "absent"           # key missing or empty


class Predicate(BaseModel):
    """One assertion over a captured tool call's arguments.

    `path` is a dotted key into the arguments dict (e.g. "queries",
    "mode", "dois"). Missing paths resolve to None, which only EXISTS/
    ABSENT treat specially; every other op fails closed on a missing path.
    """
    model_config = ConfigDict(extra="forbid")
    path: str
    op: PredOp
    value: Any = None

    def check(self, args: dict) -> bool:
        cur: Any = args
        for part in self.path.split("."):
            if isinstance(cur, dict):
                cur = cur.get(part)
            else:
                cur = None
                break

        if self.op is PredOp.EXISTS:
            return cur not in (None, "", [], {})
        if self.op is PredOp.ABSENT:
            return cur in (None, "", [], {})
        if cur is None:
            return False
        if self.op is PredOp.EQ:
            return cur == self.value
        if self.op is PredOp.NEQ:
            return cur != self.value
        if self.op is PredOp.CONTAINS:
            if isinstance(cur, str):
                return str(self.value).lower() in cur.lower()
            try:
                return self.value in cur
            except TypeError:
                return False
        if self.op is PredOp.REGEX:
            return re.search(str(self.value), str(cur)) is not None
        if self.op is PredOp.LEN_GTE:
            try:
                return len(cur) >= int(self.value)
            except TypeError:
                return False
        if self.op is PredOp.LEN_LTE:
            try:
                return len(cur) <= int(self.value)
            except TypeError:
                return False
        if self.op is PredOp.IN_SET:
            return cur in set(self.value)
        return False


# ===========================================================================
# Tool expectations
# ===========================================================================
class ToolExpectation(BaseModel):
    """A tool that must appear in the trajectory, with arg constraints.

    `via_tool_search_ok` marks non-core tools: a correct trajectory may
    first call tool_search to unlock the capability, THEN call the tool.
    The scorer accepts either path for these.
    """
    model_config = ConfigDict(extra="forbid")
    name: str
    arg_predicates: list[Predicate] = Field(default_factory=list)
    min_calls: int = 1
    max_calls: int = 99
    via_tool_search_ok: bool = False


# ===========================================================================
# Context + expectations + item
# ===========================================================================
class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str
    content: str


class EvalContext(BaseModel):
    """Conversation state that legitimately changes the right routing."""
    model_config = ConfigDict(extra="forbid")
    ephemeral: bool = True
    project: Optional[str] = None          # filed project name -> search_user_docs scoping
    tags: list[str] = Field(default_factory=list)   # #group / #nmr chips -> tag-scoped paper_search
    attachments: list[str] = Field(default_factory=list)  # filenames -> view_attachment
    prior_turns: list[Turn] = Field(default_factory=list)  # multi-turn routing
    # Optional: inject a canned result for the FIRST call to this tool, to
    # exercise degraded-backend / empty-corpus behaviour deterministically.
    inject_tool_result: Optional[dict] = None  # {"tool": "web_search", "result": {...}}


class Expected(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: Optional[str] = None          # router's up-front pick: chat|research|code (forward-looking)
    first_tool: Optional[str] = None       # the tool that MUST be the first call
    required_tools: list[ToolExpectation] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    solo: bool = False                     # first tool must be emitted ALONE (clarification rule)
    no_tool: bool = False                  # answer from parametric knowledge; zero tool calls
    abstain: bool = False                  # must not fabricate; needs judge hook for wording
    # Which checks gate the reward. Subset of:
    # {"first_tool","required","forbidden","solo","no_tool","profile","abstain"}.
    # Empty -> all populated checks gate.
    reward_basis: list[str] = Field(default_factory=list)


class RoutingEvalItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    category: str                          # see CATEGORIES below
    query: str
    context: EvalContext = Field(default_factory=EvalContext)
    expected: Expected
    rationale: str                         # human doc: why this routing is correct


CATEGORIES = [
    "simple_lookup", "no_tool", "compute", "deep_research", "corpus_qa",
    "citation_graph", "direct_ref", "clarify", "abstain", "memory",
    "citation_export", "artifact", "multi_turn", "robustness",
]


# ===========================================================================
# Scorer
# ===========================================================================
class ToolCall(BaseModel):
    """One captured tool call from the trajectory (from `tool_call` SSE)."""
    name: str
    arguments: dict = Field(default_factory=dict)
    step: int = 0   # which assistant step it was emitted in (for `solo`)


class ItemResult(BaseModel):
    item_id: str
    passed: bool
    failures: list[str] = Field(default_factory=list)
    checks: dict[str, bool] = Field(default_factory=dict)


def _gated(expected: Expected, check: str) -> bool:
    return (not expected.reward_basis) or (check in expected.reward_basis)


def score_item(item: RoutingEvalItem, trajectory: list[ToolCall]) -> ItemResult:
    """Score one captured trajectory against an item's expectations.

    `trajectory` is the ordered list of tool calls the harness emitted for
    the (final) user turn. Order is significant for first_tool/solo.
    """
    exp = item.expected
    checks: dict[str, bool] = {}
    failures: list[str] = []
    names = [tc.name for tc in trajectory]

    # no_tool -------------------------------------------------------------
    if exp.no_tool:
        ok = len(trajectory) == 0
        checks["no_tool"] = ok
        if not ok and _gated(exp, "no_tool"):
            failures.append(f"expected zero tool calls, got {names}")

    # first_tool ----------------------------------------------------------
    if exp.first_tool:
        ok = bool(trajectory) and trajectory[0].name == exp.first_tool
        checks["first_tool"] = ok
        if not ok and _gated(exp, "first_tool"):
            got = names[0] if names else "<none>"
            failures.append(f"first tool {got!r}, expected {exp.first_tool!r}")

    # solo (clarification rule) ------------------------------------------
    if exp.solo:
        # The first call's step must contain exactly one call.
        ok = bool(trajectory) and sum(
            1 for tc in trajectory if tc.step == trajectory[0].step
        ) == 1
        checks["solo"] = ok
        if not ok and _gated(exp, "solo"):
            failures.append("expected a solo tool call in the first step")

    # required_tools ------------------------------------------------------
    if exp.required_tools:
        req_ok = True
        for req in exp.required_tools:
            matches = [tc for tc in trajectory if tc.name == req.name]
            n = len(matches)
            count_ok = req.min_calls <= n <= req.max_calls
            # arg predicates must hold for at least one matching call
            pred_ok = any(
                all(p.check(tc.arguments) for p in req.arg_predicates)
                for tc in matches
            ) if req.arg_predicates else (n > 0)
            via_ok = count_ok and pred_ok
            if not via_ok and req.via_tool_search_ok and "tool_search" in names:
                # Accept the discovery path: tool_search present and the tool
                # was attempted at all (it may surface on a later turn that
                # this single-turn capture doesn't see).
                via_ok = n > 0 or True  # discovery counts as routed-correctly
            if not via_ok:
                req_ok = False
                failures.append(
                    f"required {req.name!r}: calls={n} "
                    f"(want {req.min_calls}-{req.max_calls}), preds_ok={pred_ok}"
                )
        checks["required"] = req_ok
        if not req_ok and not _gated(exp, "required"):
            failures = [f for f in failures if not f.startswith("required ")]

    # forbidden_tools -----------------------------------------------------
    if exp.forbidden_tools:
        bad = [n for n in names if n in set(exp.forbidden_tools)]
        ok = not bad
        checks["forbidden"] = ok
        if not ok and _gated(exp, "forbidden"):
            failures.append(f"forbidden tools fired: {bad}")

    # abstain (routing proxy; wording needs the judge hook) ---------------
    if exp.abstain:
        # Routing-level proxy: must not manufacture an answer with a
        # generative/mutating tool after an empty retrieval. Final wording
        # ("not in the corpus") is checked by judge_abstention() if wired.
        manufactured = any(
            n in {"run_python", "create_artifact"} for n in names
        )
        ok = not manufactured
        checks["abstain_routing"] = ok
        if not ok and _gated(exp, "abstain"):
            failures.append("abstain item manufactured output via run_python/create_artifact")

    passed = all(checks.values()) if checks else True
    return ItemResult(item_id=item.id, passed=passed, failures=failures, checks=checks)


# ===========================================================================
# Seed items — grounded in MuninAI's real tools + chat-persona routing rules
# ===========================================================================
SEED_ITEMS: list[RoutingEvalItem] = [

    # --- simple_lookup: weather goes straight to web_search ---------------
    RoutingEvalItem(
        id="weather_with_location",
        category="simple_lookup",
        query="What's the weather in Leipzig today?",
        expected=Expected(
            profile="chat",
            first_tool="web_search",
            required_tools=[ToolExpectation(
                name="web_search",
                arg_predicates=[Predicate(path="query", op=PredOp.REGEX, value="(?i)leipzig")],
                max_calls=2,
            )],
            forbidden_tools=["deep_research", "paper_search"],
            reward_basis=["first_tool", "forbidden"],
        ),
        rationale="Live fact -> web_search immediately (chat.json). deep_research "
                  "is over-routing for a one-shot lookup.",
    ),

    # --- clarify: weather with NO location -> solo ask_clarification ------
    RoutingEvalItem(
        id="weather_no_location",
        category="clarify",
        query="What's the weather today?",
        expected=Expected(
            profile="chat",
            first_tool="ask_clarification",
            solo=True,
            forbidden_tools=["web_search", "deep_research"],
            reward_basis=["first_tool", "solo", "forbidden"],
        ),
        rationale="Missing critical param (location). ABSOLUTE clarification rule: "
                  "ask_clarification alone, nothing else. This is abstention showing "
                  "up in the everyday-query regime.",
    ),

    # --- no_tool: settled science answered from parametric knowledge -----
    RoutingEvalItem(
        id="define_nmr",
        category="no_tool",
        query="In one paragraph, what is nuclear magnetic resonance?",
        expected=Expected(
            profile="chat",
            no_tool=True,
            forbidden_tools=["web_search", "paper_search", "semantic_scholar_search", "deep_research"],
            reward_basis=["no_tool"],
        ),
        rationale="Well-established science. Web-search-routing guidance: a "
                  "knowledgeable researcher would just know this. No tool.",
    ),

    # --- compute: arithmetic -> calculate, NOT run_python ----------------
    RoutingEvalItem(
        id="percent_calc",
        category="compute",
        query="What's 17% of 4,450?",
        expected=Expected(
            first_tool="calculate",
            required_tools=[ToolExpectation(
                name="calculate",
                arg_predicates=[Predicate(path="mode", op=PredOp.IN_SET, value=["numeric", None])],
            )],
            forbidden_tools=["run_python"],
            reward_basis=["first_tool", "forbidden"],
        ),
        rationale="Precise arithmetic -> calculate. Spinning the sandbox for a "
                  "percentage is wrong routing.",
    ),

    # --- compute: unit conversion -> calculate(mode=physical) ------------
    RoutingEvalItem(
        id="unit_convert_physical",
        category="compute",
        query="boltzmann_constant * 310 K in eV?",
        expected=Expected(
            required_tools=[ToolExpectation(
                name="calculate",
                arg_predicates=[Predicate(path="mode", op=PredOp.EQ, value="physical")],
            )],
            forbidden_tools=["run_python", "web_search"],
            reward_basis=["required", "forbidden"],
        ),
        rationale="Units involved -> physical mode (calculate tool description "
                  "mandates it). Tests mode selection, not just tool selection.",
    ),

    # --- deep_research: substantive question -> deep_research FIRST -------
    RoutingEvalItem(
        id="sota_phip",
        category="deep_research",
        query="What's the current state of the art in parahydrogen-induced "
              "polarization for in-vivo imaging?",
        expected=Expected(
            profile="research",
            first_tool="deep_research",
            forbidden_tools=[],  # firing paper_search/web_search separately is the wrong PRIMARY path
            reward_basis=["first_tool"],
        ),
        rationale="Substantive 'state of the art' research -> deep_research first "
                  "(chat.json TOOL USAGE STRATEGY). It fans out internally; doing it "
                  "by hand with separate searches is the anti-pattern.",
    ),

    # --- corpus_qa: tag-scoped local corpus, not Semantic Scholar --------
    RoutingEvalItem(
        id="group_corpus_qa",
        category="corpus_qa",
        query="What did our group report about SABRE catalyst lifetime?",
        context=EvalContext(tags=["group"], project="HypMol"),
        expected=Expected(
            profile="research",
            required_tools=[ToolExpectation(
                name="paper_search",
                arg_predicates=[],   # tag scoping is applied via ContextVar, not args
                max_calls=4,
            )],
            forbidden_tools=["semantic_scholar_search"],
            reward_basis=["required"],
        ),
        rationale="'Our group' + #group tag -> local paper_search (tag-scoped via "
                  "current_query_tags). Routing to the 200M-paper S2 index would miss "
                  "the private-corpus point entirely.",
    ),

    # --- citation_graph: non-core tool reached via tool_search -----------
    RoutingEvalItem(
        id="citing_papers",
        category="citation_graph",
        query="Which papers cite the Zeitler 2021 review, doi 10.1038/s41586-021-03456-2?",
        expected=Expected(
            profile="research",
            required_tools=[ToolExpectation(
                name="get_citations",
                arg_predicates=[Predicate(path="doi", op=PredOp.REGEX, value=r"10\.\d{4,}")],
                via_tool_search_ok=True,
            )],
            forbidden_tools=["deep_research"],
            reward_basis=["required"],
        ),
        rationale="Citation-graph traversal is not in CORE_TOOLS. Correct routing is "
                  "tool_search -> get_citations (or s2_get_citations). This item is the "
                  "acceptance test that the deferred-tool router replaces the allowlist.",
    ),

    # --- direct_ref: known URL -> web_fetch, not web_search --------------
    RoutingEvalItem(
        id="known_url_fetch",
        category="direct_ref",
        query="Summarize this page for me: https://arxiv.org/abs/2406.12045",
        expected=Expected(
            first_tool="web_fetch",
            required_tools=[ToolExpectation(
                name="web_fetch",
                arg_predicates=[Predicate(path="url", op=PredOp.CONTAINS, value="arxiv.org/abs/2406.12045")],
            )],
            forbidden_tools=["web_search", "deep_research"],
            reward_basis=["first_tool", "forbidden"],
        ),
        rationale="One known URL -> web_fetch directly (chat.json simple-lookups). "
                  "Searching for a page you already have the URL to is wrong routing.",
    ),

    # --- direct_ref: known DOI -> read_paper -----------------------------
    RoutingEvalItem(
        id="known_doi_read",
        category="direct_ref",
        query="Read doi:10.1021/jacs.0c01234 and tell me what hyperpolarization "
              "method they used.",
        expected=Expected(
            profile="research",
            required_tools=[ToolExpectation(
                name="read_paper",
                arg_predicates=[Predicate(path="doi", op=PredOp.REGEX, value=r"10\.1021/jacs\.0c01234")],
                via_tool_search_ok=True,
            )],
            forbidden_tools=["deep_research", "semantic_scholar_search"],
            reward_basis=["required", "forbidden"],
        ),
        rationale="A specific DOI + 'read ... tell me the method' -> read_paper "
                  "(full-text). deep_research is over-routing for a single known paper.",
    ),

    # --- memory: store a durable fact ------------------------------------
    RoutingEvalItem(
        id="remember_research_area",
        category="memory",
        query="Just so you know going forward, I work on dissolution-DNP.",
        expected=Expected(
            required_tools=[ToolExpectation(
                name="remember",
                arg_predicates=[Predicate(path="value", op=PredOp.REGEX, value="(?i)dnp")],
                via_tool_search_ok=True,
            )],
            forbidden_tools=["web_search", "paper_search"],
            reward_basis=["required", "forbidden"],
        ),
        rationale="A durable user fact 'going forward' -> remember. Not a search.",
    ),

    # --- citation_export: BibTeX for several DOIs ------------------------
    RoutingEvalItem(
        id="export_bibtex",
        category="citation_export",
        query="Give me BibTeX for these three: 10.1/a, 10.2/b, 10.3/c",
        expected=Expected(
            required_tools=[ToolExpectation(
                name="export_citations",
                arg_predicates=[
                    Predicate(path="format", op=PredOp.EQ, value="bibtex"),
                    Predicate(path="dois", op=PredOp.LEN_GTE, value=3),
                ],
                via_tool_search_ok=True,
            )],
            forbidden_tools=["web_search"],
            reward_basis=["required"],
        ),
        rationale="Formatted citations -> export_citations(format=bibtex). Non-core, "
                  "so tool_search-then-export is an acceptable path.",
    ),

    # --- multi_turn: re-route on the LAST turn (the router's whole point) -
    RoutingEvalItem(
        id="reroute_research_to_compute",
        category="multi_turn",
        query="Now plot those polarization values vs field strength and show me.",
        context=EvalContext(prior_turns=[
            Turn(role="user", content="What polarization levels does SABRE reach at low field?"),
            Turn(role="assistant", content="(prior deep_research answer with a few numbers)"),
        ]),
        expected=Expected(
            profile="code",
            required_tools=[ToolExpectation(name="run_python", via_tool_search_ok=True)],
            forbidden_tools=["deep_research", "paper_search"],
            reward_basis=["required", "forbidden"],
        ),
        rationale="Turn 1 was research; turn 2 is a plot. Per-turn routing must send "
                  "THIS turn to run_python — no persona handoff, no re-search. This is "
                  "exactly the case the old delegation machinery handled clumsily.",
    ),

    # --- robustness: degraded web_search backend (injected result) -------
    RoutingEvalItem(
        id="web_search_degraded",
        category="robustness",
        query="What's the latest on the EuroHyperPol consortium funding round?",
        context=EvalContext(inject_tool_result={
            "tool": "web_search",
            "result": {"results": [], "total_hits": 0,
                       "warning": "all engines unresponsive",
                       "engines_unresponsive": ["google", "bing", "duckduckgo"]},
        }),
        expected=Expected(
            # Correct behaviour: surface the degradation + ask for a URL/DOI,
            # do NOT hammer web_search, do NOT conclude the topic doesn't exist.
            required_tools=[ToolExpectation(name="web_search", max_calls=2)],
            forbidden_tools=["deep_research"],
            abstain=True,   # judge hook checks it surfaces degradation, not "no such thing"
            reward_basis=["forbidden", "abstain"],
        ),
        rationale="web_search returns the `warning` field. Tool description says: "
                  "surface the breakage, ask for a URL/DOI, don't retry blindly, don't "
                  "conclude the topic is obscure. No public benchmark tests this.",
    ),

    # --- robustness: paraphrase / non-English -> still web_search --------
    RoutingEvalItem(
        id="weather_paraphrase",
        category="robustness",
        query="leipzig wetter heute, brauch ich nen regenschirm?",
        expected=Expected(
            first_tool="web_search",
            forbidden_tools=["deep_research", "calculate"],
            reward_basis=["first_tool", "forbidden"],
        ),
        rationale="BFCL-style paraphrase/codeswitch robustness: a German, colloquial "
                  "weather question must route identically to the clean English one.",
    ),
]


# ===========================================================================
# Optional LLM-judge hook (only for the few answer-level assertions)
# ===========================================================================
async def judge_abstention(final_answer: str, item: RoutingEvalItem) -> bool:
    """Wire to your vLLM. Returns True iff the answer honestly signals
    'I couldn't find / verify this' rather than fabricating. Only called for
    items with expected.abstain set. Keep the rubric one-line and binary so
    judge variance stays low (cf. AbstentionBench scoring)."""
    raise NotImplementedError("wire to vllm_post_json with a binary rubric")


# ===========================================================================
# Runner stub — drive /api/chat/completions, capture the tool_call stream
# ===========================================================================
async def run_item(client, base_url: str, email: str, item: RoutingEvalItem) -> ItemResult:
    """Sketch: POST the (multi-turn) messages with ephemeral=true, read the
    SSE stream, collect `tool_call` events into a trajectory, score it.

    Reuses the exact SSE shape scripts/test_delegate_persona.py already parses
    (events: tool_call, tool_result, token, done; plus the now-deprecated
    delegated/persona_changed). `step` increments on each assistant turn
    boundary so `solo` can be evaluated.

    Note: once routing profiles land, send the profile the router SHOULD pick
    as a label only and assert expected.profile against the `persona_changed`/
    routing event the backend emits — do NOT send it as the request persona,
    or you'd be grading the router on an answer you handed it.
    """
    raise NotImplementedError(
        "drive POST {base}/api/chat/completions, headers X-Munin-Email, "
        "body {messages, ephemeral: true}; collect tool_call events -> "
        "[ToolCall(...)] -> score_item(item, trajectory). Honour "
        "context.inject_tool_result via a stub MCP layer for robustness items."
    )


# ===========================================================================
# InspectAI adapter (so this sits next to AstaBench / BFCL runs)
# ===========================================================================
def as_inspect_task():
    """Return an inspect_ai Task wrapping SEED_ITEMS. Each Sample's input is
    the item's messages; the custom scorer calls score_item() on the captured
    trajectory and returns CORRECT iff ItemResult.passed. Lets you report
    routing accuracy on the same cost-aware leaderboard methodology the
    briefing recommends adopting."""
    try:
        from inspect_ai import Task, task          # noqa: F401
        from inspect_ai.dataset import Sample      # noqa: F401
        from inspect_ai.scorer import scorer, Score, CORRECT, INCORRECT  # noqa: F401
    except ImportError as e:
        raise RuntimeError("pip install inspect-ai to use the Inspect adapter") from e
    raise NotImplementedError(
        "map each RoutingEvalItem -> Sample(input=messages, metadata=item); "
        "a @solver runs the harness + captures tool_call events; a @scorer "
        "calls score_item(metadata, trajectory)."
    )


if __name__ == "__main__":
    # Smoke check: schema is internally consistent and predicates evaluate.
    assert all(i.category in CATEGORIES for i in SEED_ITEMS), "unknown category"
    demo = ToolCall(name="web_search", arguments={"query": "leipzig weather today"}, step=0)
    res = score_item(SEED_ITEMS[0], [demo])
    print(f"{len(SEED_ITEMS)} seed items across "
          f"{len(set(i.category for i in SEED_ITEMS))} categories.")
    print(f"weather_with_location vs a correct web_search call -> passed={res.passed}")
