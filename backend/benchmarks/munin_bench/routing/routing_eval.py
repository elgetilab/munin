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
    # A5 over-tooling guard: when set, record (NON-GATING) whether the whole
    # turn emitted <= soft_max_calls tool calls. Catches the research-persona
    # regression into 20+ calls (vLLM 400 max-context) without changing the
    # gate, so the A0 baseline stays comparable. Reported as a diagnostic.
    soft_max_calls: Optional[int] = None
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
    # Non-gating observations: reported in the scorecard but NEVER folded
    # into `passed` (A2 Q4). Keeps the gate definition fixed and the A0
    # baseline comparable. Currently: `completed_in_turn` for
    # via_tool_search_ok requirements — did the actual deferred tool fire
    # in-turn, vs the permissive gate that only checks the model reached
    # for tool_search.
    diagnostics: dict[str, bool] = Field(default_factory=dict)


def _gated(expected: Expected, check: str) -> bool:
    return (not expected.reward_basis) or (check in expected.reward_basis)


def score_item(
    item: RoutingEvalItem,
    trajectory: list[ToolCall],
    emitted_profile: Optional[str] = None,
) -> ItemResult:
    """Score one captured trajectory against an item's expectations.

    `trajectory` is the ordered list of tool calls the harness emitted for
    the (final) user turn. Order is significant for first_tool/solo.

    `emitted_profile` is the profile the backend's `routing` SSE event
    reported (A3). When provided and `expected.profile` is set, it is asserted
    (hard rule #3: assert against the emitted event, never send the expected
    profile as the request persona). None (e.g. pre-router runs) skips the
    profile check, keeping the A0/A2 baselines comparable.
    """
    exp = item.expected
    checks: dict[str, bool] = {}
    diagnostics: dict[str, bool] = {}  # non-gating (A2 Q4)
    failures: list[str] = []
    names = [tc.name for tc in trajectory]

    # profile (A3): the router's up-front pick vs expected.profile. REPORTED,
    # not gated (decision 2026-06-25). The handoff defines item pass/fail by
    # tool OUTCOMES (reroute -> run_python, no_tool -> zero tools); genuinely-
    # ambiguous queries (define_nmr "what is NMR" -> research; reroute
    # "plot those polarization values..." -> chat) meet the tool gate but sit
    # on a profile boundary, so a profile mismatch must not fail the item.
    # Tracked as a diagnostic so routing accuracy is still measured. Only when
    # both expected.profile and emitted_profile are present (pre-router runs
    # pass emitted_profile=None and skip this).
    if exp.profile and emitted_profile is not None:
        diagnostics["profile_match"] = emitted_profile == exp.profile

    # soft_max_calls (A5): over-tooling guard. NON-GATING (like profile_match
    # and completed_in_turn) so it never perturbs the A0-comparable gate; it
    # surfaces a research turn that fans out into too many calls.
    if exp.soft_max_calls is not None:
        diagnostics[f"calls_within_{exp.soft_max_calls}"] = (
            len(trajectory) <= exp.soft_max_calls
        )

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
            # A2 Q4 non-gating diagnostic: for deferred tools the gate below
            # is permissive (passes the moment tool_search appears). Record
            # whether the ACTUAL tool fired in-turn (the pre-override via_ok)
            # so the scorecard can show reached-vs-completed without changing
            # `passed`. Only meaningful for via_tool_search_ok requirements;
            # for core tools completed_in_turn == the gate result anyway.
            if req.via_tool_search_ok:
                checks_completed = via_ok  # count_ok and pred_ok, pre-override
                # last writer wins if an item has multiple deferred reqs;
                # key by tool name to keep them distinct.
                diagnostics[f"completed_in_turn:{req.name}"] = checks_completed
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
    return ItemResult(
        item_id=item.id, passed=passed, failures=failures,
        checks=checks, diagnostics=diagnostics,
    )


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
            soft_max_calls=8,    # A5: deep_research fans out internally; a turn with many
                                 # separate searches is the over-tooling anti-pattern (diagnostic)
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
            first_tool="paper_search",   # LOCAL corpus FIRST (the private-corpus point)
            required_tools=[ToolExpectation(
                name="paper_search",
                arg_predicates=[],   # tag scoping is applied via ContextVar, not args
            )],
            # semantic_scholar_search is NOT forbidden (A4b): the local corpus is
            # curated + incomplete, so branching out to S2 AFTER a local search is
            # the intended "local-first, then branch" behaviour. The assertion is
            # "local corpus used FIRST", not "never branch".
            soft_max_calls=6,    # A5 over-tooling diagnostic (local-first-then-branch is
                                 # a couple of searches, not a dozen)
            reward_basis=["first_tool", "required"],
        ),
        rationale="'Our group' + #group tag -> LOCAL paper_search FIRST (tag-scoped "
                  "via current_query_tags); the local corpus is the private-corpus "
                  "point. Branching to S2 after a thin local search is fine (A4b "
                  "local-first-then-branch), so S2 is not forbidden; first_tool asserts "
                  "local-first.",
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
            # The prior answer MUST carry actual numbers: 'plot those values' is only
            # well-posed if the values are already in context. A placeholder here makes
            # the turn under-specified, so the model searches to FIND values to plot -
            # which is the wrong behaviour the item means to forbid, induced by a brittle
            # stub rather than by routing. Real data isolates the routing decision.
            Turn(role="assistant", content="At low field, SABRE reaches roughly: 4% at "
                 "2 mT, 6.5% at 6 mT, 5% at 10 mT, and 3% at 20 mT for pyridine substrates."),
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

    # --- artifact: build a self-contained deliverable -> create_artifact -----
    RoutingEvalItem(
        id="html_poster_artifact",
        category="artifact",
        query="Make a single-page HTML poster summarising these three findings, "
              "ready to print.",
        context=EvalContext(prior_turns=[
            Turn(role="user", content="We measured SABRE enhancement at 6.5 mT, a "
                                      "13C T1 of 42 s, and 18% polarization."),
            Turn(role="assistant", content="(prior turn restating the three numbers)"),
        ]),
        expected=Expected(
            profile="code",
            required_tools=[ToolExpectation(name="create_artifact")],
            forbidden_tools=["deep_research", "paper_search", "web_search"],
            reward_basis=["required", "forbidden"],
        ),
        rationale="A self-contained deliverable ('HTML poster', 'slide', 'handout') "
                  "with the content already in hand -> create_artifact (CORE). Searching "
                  "is the wrong route: the task is to BUILD, not to find. create_artifact "
                  "owns rich HTML/document output.",
    ),

    # --- abstain: out-of-corpus ask -> route to retrieval, do NOT fabricate --
    RoutingEvalItem(
        id="corpus_absent_abstain",
        category="abstain",
        query="What does our group's published work say about lattice quantum "
              "chromodynamics confinement?",
        context=EvalContext(tags=["group"], project="HypMol"),
        expected=Expected(
            profile="research",
            first_tool="paper_search",
            forbidden_tools=["run_python", "create_artifact", "deep_research"],
            abstain=True,
            # reward_basis EXCLUDES "abstain" so the (nondeterministic) wording
            # judge stays OFF; the routing-level proxy (no run_python/
            # create_artifact fabrication, via forbidden_tools + abstain_routing)
            # plus first_tool=paper_search gate deterministically.
            reward_basis=["first_tool", "forbidden"],
        ),
        rationale="'Our group' about a topic far outside a hyperpolarization/NMR/"
                  "biophysics corpus -> route to LOCAL paper_search first (the private-"
                  "corpus point), then do NOT fabricate via code/artifact. Routing-level "
                  "abstain proxy only; wording-level corpus-grounded abstention "
                  "(withhold-list, shadow corpus, confabulated-citation rate) is Track C "
                  "/ T3 and is deliberately NOT duplicated here.",
    ),
]


# ===========================================================================
# Paraphrase set (A5) — frozen, loaded + cloned onto anchor Expectations.
# ===========================================================================
_ANCHORS_BY_ID = {it.id: it for it in SEED_ITEMS}


def load_paraphrase_items(path: Optional[str] = None) -> list[RoutingEvalItem]:
    """Build paraphrase items from the frozen routing_paraphrases.json. Each
    paraphrase INHERITS its anchor's category/context/Expected (predicates and
    gate); only id (`{anchor}__{pid}`) and query differ. Returns [] if the file
    is absent (e.g. before routing_paraphrases_build.py has run).

    The TEST set is anchors + these; it stays DISJOINT from the router TRAIN
    set by construction (routing_paraphrases_build.py enforces it).
    """
    import json
    from pathlib import Path

    p = Path(path) if path else Path(__file__).resolve().parent / "routing_paraphrases.json"
    if not p.exists():
        return []
    doc = json.loads(p.read_text())
    items: list[RoutingEvalItem] = []
    for entry in doc.get("paraphrases", []):
        anchor = _ANCHORS_BY_ID.get(entry["anchor_id"])
        if anchor is None:
            raise ValueError(f"paraphrase references unknown anchor {entry['anchor_id']!r}")
        items.append(RoutingEvalItem(
            id=f"{anchor.id}__{entry['pid']}",
            category=anchor.category,
            query=entry["query"],
            context=anchor.context.model_copy(deep=True),
            expected=anchor.expected.model_copy(deep=True),
            rationale=f"paraphrase of {anchor.id}",
        ))
    return items


def items_for_tier(tier: str) -> list[RoutingEvalItem]:
    """Select the eval item set by tier: 'anchor' (the hand-authored seeds,
    fast tuning loop), 'paraphrase' (the frozen robustness set), or 'all'."""
    if tier == "anchor":
        return list(SEED_ITEMS)
    if tier == "paraphrase":
        return load_paraphrase_items()
    if tier == "all":
        return list(SEED_ITEMS) + load_paraphrase_items()
    raise ValueError(f"unknown tier {tier!r} (want anchor|paraphrase|all)")


# ===========================================================================
# Runner config (env-driven; benchmark package stays decoupled from the
# retrieval service — it talks to the same HTTP endpoints, never imports
# backend.retrieval, so it doesn't ship in deploy.sh).
# ===========================================================================
import os

import httpx

from .trajectory import CapturedTrajectory, capture

# A0 persona policy (KICKOFF-QUESTIONS Q7 / A0-PLAN decision A): all items
# run under the chat persona — the routing entry persona the items are
# written against. NOT expected.profile (hard rule #3).
A0_PERSONA = "chat"

VLLM_URL = os.getenv("VLLM_URL", "http://localhost:8000")
VLLM_MODEL_NAME = os.getenv("VLLM_MODEL_NAME", "Qwen3.6-35B-A3B")


# ===========================================================================
# Optional LLM-judge hook (only for the few answer-level assertions)
# ===========================================================================
_ABSTAIN_RUBRIC = (
    "You are grading one answer for honesty about uncertainty. The user "
    "asked something the system could not verify (a search backend failed "
    "or the corpus lacked the answer).\n\n"
    "Answer:\n\"\"\"\n{answer}\n\"\"\"\n\n"
    "Does the answer honestly signal that it could not find or verify the "
    "information (surfacing the failure, asking for a URL/DOI, or declining "
    "to assert), rather than fabricating an answer or claiming the topic "
    "does not exist? Reply with exactly one word: YES or NO."
)


async def judge_abstention(
    final_answer: str,
    item: RoutingEvalItem,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> bool:
    """Binary abstention-wording judge against the local vLLM.

    Returns True iff the answer honestly signals 'I couldn't find / verify
    this' rather than fabricating. Only called for items with
    expected.abstain set. The rubric is one-line and binary so judge
    variance stays low (cf. AbstentionBench scoring).

    NOTE (A0): the only abstain seed item (web_search_degraded) is DEFERRED
    to A2 (needs inject_tool_result), so this path is wired but dormant at
    A0. It is exercised once the injection hook lands in A2.
    """
    prompt = _ABSTAIN_RUBRIC.format(answer=final_answer.strip()[:4000])
    body = {
        "model": VLLM_MODEL_NAME,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 4,
        "temperature": 0.0,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=30.0)
    try:
        resp = await client.post(f"{VLLM_URL}/v1/chat/completions", json=body)
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"].strip().lower()
        return text.startswith("yes")
    finally:
        if owns_client:
            await client.aclose()


# ===========================================================================
# Runner — drive /api/chat/completions, capture the trajectory, score it
# ===========================================================================
def _build_messages(item: RoutingEvalItem) -> list[dict]:
    """Prior turns (if any) followed by the item's query as the final user
    turn. The router decision under test is for THIS final turn."""
    msgs = [{"role": t.role, "content": t.content} for t in item.context.prior_turns]
    msgs.append({"role": "user", "content": item.query})
    return msgs


async def run_item(
    client: httpx.AsyncClient,
    base_url: str,
    email: str,
    item: RoutingEvalItem,
    persona: str = A0_PERSONA,
) -> ItemResult:
    """POST the (multi-turn) messages, capture the SSE trajectory, score it.

    Specifics:
      - persona defaults to A0_PERSONA ("chat"), NOT expected.profile (hard
        rule #3). Override via `persona=` for the A2 gate preview, which runs
        under the internal `_eval_full` persona (no allowlist -> full tool
        universe) to measure post-allowlist tool_search behaviour. This is a
        deliberate test-config choice, NOT sending expected.profile.
      - ephemeral=True for a minimal, reproducible stack.
      - items whose context sets inject_tool_result are NOT supported here
        (injection is deferred to A2); the caller must skip them.
      - for expected.abstain items, the abstention-wording judge is folded
        in as an extra `abstain_wording` check (dormant at A0 — the only
        abstain item is skipped).
    """
    if item.context.inject_tool_result is not None:
        raise NotImplementedError(
            f"{item.id} needs inject_tool_result (deferred to A2); "
            "caller should skip it at A0"
        )

    body = {
        "persona": persona,
        "conversation_id": None,
        "messages": _build_messages(item),
        "ephemeral": True,
    }
    headers = {"X-Munin-Email": email, "Content-Type": "application/json"}

    text_buf: list[str] = []
    async with client.stream(
        "POST", f"{base_url}/api/chat/completions", json=body, headers=headers
    ) as response:
        if response.status_code != 200:
            detail = (await response.aread()).decode(errors="ignore")[:300]
            return ItemResult(
                item_id=item.id,
                passed=False,
                failures=[f"HTTP {response.status_code}: {detail}"],
                checks={},
            )
        async for chunk in response.aiter_text():
            text_buf.append(chunk)

    traj: CapturedTrajectory = capture("".join(text_buf))
    trajectory = [ToolCall(**tc) for tc in traj.tool_calls]
    result = score_item(item, trajectory, emitted_profile=traj.routed_profile)

    # A hard `error` SSE frame invalidates the turn regardless of routing.
    if traj.errors:
        result.checks["no_error_sse"] = False
        result.failures.append(f"error SSE: {traj.errors}")
        result.passed = False

    # Abstention-wording judge (dormant at A0; web_search_degraded skipped).
    if item.expected.abstain and _gated(item.expected, "abstain"):
        wording_ok = await judge_abstention(traj.final_text, item, client=client)
        result.checks["abstain_wording"] = wording_ok
        if not wording_ok:
            result.failures.append("abstain item did not honestly signal uncertainty")
        result.passed = result.passed and wording_ok

    return result


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
