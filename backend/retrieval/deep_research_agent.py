"""
Deep Research agent: open question -> composite document synthesised from many
sources, via a plan-as-data-structure loop over the search + source agents.

This is the long-running, semi-pinned agent from AGENT-IMPLEMENTATION-PLAN.md
(D1-D13). It is NOT a normal in-turn MCP tool (a multi-minute run cannot block a
chat turn) and NOT a SLURM job (D1): it runs in-process against the live vLLM.
This module is the RESEARCH LOOP; the chat-service integration layer (detached
asyncio task, SSE progress + stream_registry resume, the on/off toggle,
markdown-artifact delivery, configurable-N concurrency) and the external-benchmark
eval (D13) are the deferred layer around it.

The loop (semi-pinned per iteration - only "what are the sub-questions" is free):

    decompose question -> plan[{sub_question, status, notes[], evidence_refs[]}]
    for each open sub-question:
        search  ->  screen (batched, recall-tuned)  ->  source(qa) fan-out
        -> structured notes {claim, value?, unit?, quote, ref, sub_question_id}
        -> status = resolved | unresolvable
    synthesise sections from notes (grouped by sub_question_id; name contradictions)
    -> markdown document + citations carrying read_depth

Key decisions realised here: plan-as-data-structure (auditable + resumable, D7),
batched recall-tuned screener that logs every drop (D8), funnel widths as config
(D9), read_depth per citation (D11), structured contradiction-aware notes (D12),
synthesiser drafts from quotes not paraphrase-of-paraphrase. Plan is checkpointed
to JSON for resume (SQLite conversation_plans migration is a follow-up).
"""

from __future__ import annotations

import json
import os
import re
import uuid
from typing import Any, Awaitable, Callable, Optional

import httpx

from agent_trace import AgentTrace
from database import VLLM_MODEL_NAME, VLLM_URL

# Async progress callback: `await progress(event: str, data: dict)`. The manager
# passes one that appends a render-ready event to the durable job log (so the
# frontend renders the run inline, and a reconnect replays it); None in a
# bare/eval call. Async so events persist IN ORDER (a fire-and-forget task could
# reorder the log). Non-fatal - a bad callback must never kill the run.
ProgressFn = Callable[[str, dict], Awaitable[None]]


async def _emit(progress: Optional[ProgressFn], event: str, **data: Any) -> None:
    if progress is None:
        return
    try:
        await progress(event, data)
    except Exception:
        pass

# Funnel widths are CONFIG, not agent-chosen (D9) - this is what makes cost
# predictable. Defaults are modest; a full run widens SCREEN_KEEP/READ_CAP.
DEFAULT_MAX_SUBQ = 5
DEFAULT_SCREEN_KEEP = 12     # candidates kept per sub-question after screening
DEFAULT_READ_CAP = 4         # full source(qa) reads per sub-question
CHECKPOINT_DIR = os.getenv("DEEP_RESEARCH_DIR", "/data/deep_research")


async def _llm(system: str, user: str, *, max_tokens: int = 1500,
               thinking: bool = False) -> str:
    body = {"model": VLLM_MODEL_NAME,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "max_tokens": max_tokens, "temperature": 0.4, "stream": False}
    if not thinking:
        body["chat_template_kwargs"] = {"enable_thinking": False}
    async with httpx.AsyncClient(timeout=180.0) as client:
        r = await client.post(f"{VLLM_URL}/v1/chat/completions", json=body)
    if r.status_code != 200:
        raise RuntimeError(f"vLLM {r.status_code}: {r.text[:150]}")
    return (r.json()["choices"][0]["message"].get("content") or "").strip()


def _parse_json(text: str, default):
    m = re.search(r"[\[{].*[\]}]", text, re.DOTALL)
    if not m:
        return default
    try:
        return json.loads(m.group(0))
    except Exception:
        return default


async def _decompose(question: str, max_subq: int) -> list[str]:
    out = await _llm(
        "You are a research planner. Break the user's question into 2-5 concrete, "
        "independently-answerable sub-questions. Return ONLY a JSON list of strings.",
        f"Question: {question}", max_tokens=500)
    subs = _parse_json(out, [])
    subs = [str(s).strip() for s in subs if isinstance(s, str) and s.strip()]
    return subs[:max_subq] or [question]


async def _screen(sub_question: str, candidates: list[dict], keep: int,
                  tr: AgentTrace) -> list[dict]:
    """Batched, recall-tuned triage over candidate titles+snippets. A false
    negative (dropping the right paper) is silent and unrecoverable, so err
    permissive and LOG every drop (D8)."""
    if len(candidates) <= keep:
        return candidates
    listing = "\n".join(
        f"[{i}] {c.get('title','')} :: {(c.get('snippet') or '')[:160]}"
        for i, c in enumerate(candidates))
    out = await _llm(
        "You screen candidate papers for relevance to a research sub-question. "
        "ERR ON THE SIDE OF KEEPING - a wrongly dropped paper is unrecoverable. "
        "Return ONLY a JSON list of the indices to KEEP.",
        f"Sub-question: {sub_question}\n\nCandidates:\n{listing}", max_tokens=300)
    idxs = _parse_json(out, list(range(len(candidates))))
    keep_set = {i for i in idxs if isinstance(i, int) and 0 <= i < len(candidates)}
    if not keep_set:
        keep_set = set(range(len(candidates)))
    kept = [c for i, c in enumerate(candidates) if i in keep_set][:keep]
    dropped = [candidates[i].get("title") for i in range(len(candidates))
               if i not in keep_set]
    tr.decide("screen", sub_question=sub_question, kept=len(kept),
              dropped=len(dropped), dropped_titles=dropped[:20])
    return kept


def _answer_line(answer: str) -> str:
    m = re.search(r"answer:\s*(.+)", answer, re.IGNORECASE)
    return (m.group(1).strip() if m else answer.strip())[:400]


async def _resolve(node: dict, depth: str, read_cap: int, screen_keep: int,
                   tr: AgentTrace, progress: Optional[ProgressFn] = None) -> None:
    """Fill a plan node's notes + evidence_refs and set its status. Emits
    tool_call/tool_result events so the search + each paper-read render inline
    (the same cards as normal tool use)."""
    from mcp.tools.search_agent import search
    from mcp.tools.source import source

    sq = node["sub_question"]
    # search shows as a tool card.
    tc = "tc-" + uuid.uuid4().hex[:8]
    await _emit(progress, "tool_call", id=tc, name="search",
                arguments={"query": sq}, sub_question_id=node["id"])
    res = await search(query=sq, depth=depth, top_k=screen_keep + 6)
    candidates = (res or {}).get("ranked", [])
    # Keep only readable scholarly refs (a web listicle has no DOI to source()).
    candidates = [c for c in candidates if c.get("doi")]
    kept = await _screen(sq, candidates, screen_keep, tr)
    await _emit(progress, "tool_result", id=tc,
                summary=f"{len(candidates)} candidates, {len(kept)} kept to read")

    reads = 0
    for c in kept:
        if reads >= read_cap:
            break
        reads += 1
        rc = "tc-" + uuid.uuid4().hex[:8]
        title = (c.get("title") or c["doi"])[:90]
        await _emit(progress, "tool_call", id=rc, name="source",
                    arguments={"doi": c["doi"], "question": sq, "title": title},
                    sub_question_id=node["id"])
        env = await source(refs=[{"doi": c["doi"]}], mode="qa", question=sq)
        outcome = env.get("outcome")
        depth_read = env.get("read_depth")
        await _emit(progress, "tool_result", id=rc,
                    summary=f"{title} - {outcome} ({depth_read})",
                    outcome=outcome, read_depth=depth_read,
                    ref=env.get("ref_resolved"))
        node["evidence_refs"].append({"ref": env.get("ref_resolved"),
                                      "read_depth": depth_read, "outcome": outcome})
        if outcome == "resolved" and env.get("answer") and not env.get("abstained"):
            note = {"claim": _answer_line(env["answer"]), "quote": env.get("quote"),
                    "ref": env.get("ref_resolved"), "read_depth": depth_read,
                    "sub_question_id": node["id"]}
            node["notes"].append(note)
            await _emit(progress, "note", sub_question_id=node["id"],
                        claim=note["claim"], quote=note["quote"], ref=note["ref"])
    node["status"] = "resolved" if node["notes"] else "unresolvable"
    tr.decide("resolved sub-question", sub_question=sq, status=node["status"],
              n_notes=len(node["notes"]), n_reads=reads)


async def _synthesise_section(node: dict) -> str:
    notes = node["notes"]
    if not notes:
        return f"## {node['sub_question']}\n\n_No supporting evidence was found._\n"
    body = "\n".join(
        f"- claim: {n['claim']}\n  quote: \"{(n.get('quote') or '')[:300]}\"\n"
        f"  ref: {(n['ref'] or {}).get('title') or (n['ref'] or {}).get('doi')}"
        for n in notes)
    section = await _llm(
        "You draft one section of a research report from evidence notes. Draft "
        "ONLY from the quoted evidence - do not add claims not in the notes. Cite "
        "papers by short title. If the notes DISAGREE, name the split explicitly "
        "rather than averaging. 2-5 sentences.",
        f"Sub-question: {node['sub_question']}\n\nNotes:\n{body}", max_tokens=600)
    return f"## {node['sub_question']}\n\n{section}\n"


def _cite(ref: Optional[dict]) -> str:
    ref = ref or {}
    return ref.get("title") or ref.get("doi") or "source"


async def _tldr(question: str, notes: list[dict]) -> str:
    """A 3-5 bullet TL;DR of the strongest findings, grounded in the notes."""
    if not notes:
        return ""
    body = "\n".join(f"- {n['claim']} (source: {_cite(n.get('ref'))})" for n in notes[:24])
    out = await _llm(
        "Write a 3-5 bullet TL;DR for a research report from these grounded "
        "findings. Each bullet is a bold headline claim followed by one sentence, "
        "and names its source. Draft ONLY from the findings; add no new claims. "
        "Use '- ' bullets.",
        f"Question: {question}\n\nFindings:\n{body}", max_tokens=600)
    return f"## TL;DR\n\n{out}\n\n" if out else ""


def _key_findings(notes: list[dict]) -> str:
    """Numbered claim + inline citation per grounded note (deterministic)."""
    if not notes:
        return ""
    lines = [f"{i}. {n['claim']} ({_cite(n.get('ref'))})" for i, n in enumerate(notes, 1)]
    return "## Key Findings\n\n" + "\n".join(lines) + "\n\n"


def _caveats(plan: list[dict], citations: list[dict]) -> str:
    """Honest limitations: unanswered sub-questions + abstract-only citations."""
    lines: list[str] = []
    unresolved = [n["sub_question"] for n in plan if n["status"] != "resolved"]
    if unresolved:
        lines.append("- The available sources did not answer:")
        lines += [f"  - {u}" for u in unresolved]
    abstract_only = [c for c in citations if c.get("read_depth") == "abstract"]
    if abstract_only:
        lines.append(f"- {len(abstract_only)} source(s) were read at the abstract "
                     "level only, so claims resting on them are less certain.")
    return "## Caveats\n\n" + "\n".join(lines) + "\n" if lines else ""


def _citations(plan: list[dict]) -> list[dict]:
    seen, out = set(), []
    for node in plan:
        for n in node["notes"]:
            ref = n.get("ref") or {}
            key = (ref.get("doi") or ref.get("title") or "").lower()
            if key and key not in seen:
                seen.add(key)
                out.append({"ref": ref, "read_depth": n.get("read_depth")})
    return out


def _checkpoint(job_id: str, question: str, plan: list[dict]) -> None:
    try:
        os.makedirs(CHECKPOINT_DIR, exist_ok=True)
        path = os.path.join(CHECKPOINT_DIR, f"{job_id}.json")
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"job_id": job_id, "question": question, "plan": plan}, f)
        os.replace(tmp, path)
    except OSError:
        pass


def _load_checkpoint(job_id: str) -> Optional[list[dict]]:
    try:
        blob = json.load(open(os.path.join(CHECKPOINT_DIR, f"{job_id}.json")))
        return blob.get("plan")
    except Exception:
        return None


async def deep_research(question: str, *, depth: str = "normal",
                        max_subq: int = DEFAULT_MAX_SUBQ,
                        screen_keep: int = DEFAULT_SCREEN_KEEP,
                        read_cap: int = DEFAULT_READ_CAP,
                        job_id: Optional[str] = None,
                        progress: Optional[ProgressFn] = None) -> dict:
    """Run the research loop and return {document, plan, citations, trace}.

    Callable synchronously (tests/eval) or as a detached task (the manager wraps
    this). Checkpoints the plan after each sub-question so an interrupted run
    resumes instead of restarting (D6b): pass the same job_id and it reloads the
    plan and skips already-resolved sub-questions. `progress(event, data)` is
    called at each milestone for the manager's durable progress record."""
    if not question or not str(question).strip():
        return {"error": "deep_research requires a question"}
    job_id = job_id or "dr_" + uuid.uuid4().hex[:16]
    tr = AgentTrace("deep_research", question=question, job_id=job_id)

    # Resume from a checkpoint if one exists for this job_id; else plan afresh.
    plan = _load_checkpoint(job_id)
    # The plan event carries id + text + status per sub-question so the frontend
    # renders it as a live checklist (and updates each item as it resolves).
    def _plan_items() -> list[dict]:
        return [{"id": n["id"], "text": n["sub_question"], "status": n["status"]}
                for n in plan]

    if plan:
        tr.decide("resumed from checkpoint", n_sub_questions=len(plan),
                  open=sum(1 for n in plan if n["status"] == "open"))
        await _emit(progress, "plan", items=_plan_items(), resumed=True)
    else:
        subs = await _decompose(question, max_subq)
        tr.llm(1)
        plan = [{"id": f"sq{i}", "sub_question": s, "status": "open",
                 "notes": [], "evidence_refs": []} for i, s in enumerate(subs)]
        tr.decide("plan", n_sub_questions=len(plan))
        _checkpoint(job_id, question, plan)
        await _emit(progress, "plan", items=_plan_items())

    for node in plan:
        if node["status"] != "open":  # resumed: skip already-done sub-questions
            continue
        await _emit(progress, "plan_update", id=node["id"], status="in_progress",
                    sub_question=node["sub_question"])
        try:
            await _resolve(node, depth, read_cap, screen_keep, tr, progress)
        except Exception as exc:  # noqa: BLE001 - one bad sub-question shouldn't kill the run
            node["status"] = "unresolvable"
            tr.decide("sub-question failed", sub_question=node["sub_question"],
                      err=str(exc)[:160])
        _checkpoint(job_id, question, plan)
        await _emit(progress, "plan_update", id=node["id"], status=node["status"],
                    n_notes=len(node["notes"]))

    await _emit(progress, "synthesising",
                n_resolved=sum(1 for n in plan if n["status"] == "resolved"))
    # Claude-style layout, drawn only from grounded notes:
    #   Title / TL;DR / Key Findings / Details (per sub-question) / Sources / Caveats
    all_notes = [n for node in plan for n in node["notes"]]
    n_resolved = sum(1 for n in plan if n["status"] == "resolved")
    citations = _citations(plan)

    tldr_md = await _tldr(question, all_notes)
    key_findings_md = _key_findings(all_notes)
    sections = [await _synthesise_section(node) for node in plan]  # Details
    details_md = "## Details\n\n" + "\n".join(sections) if sections else ""
    # Sources: numbered, DOI-linked, with read depth (full-text vs abstract).
    sources_md = ""
    if citations:
        lines = []
        for i, c in enumerate(citations, 1):
            ref = c.get("ref") or {}
            doi = ref.get("doi")
            title = ref.get("title") or doi or "source"
            link = f"[{title}](https://doi.org/{doi})" if doi else title
            lines.append(f"{i}. {link} _(read: {c.get('read_depth') or 'unknown'})_")
        sources_md = "\n## Sources\n\n" + "\n".join(lines) + "\n"
    caveats_md = _caveats(plan, citations)

    document = (f"# {question}\n\n" + tldr_md + key_findings_md + details_md +
                sources_md + "\n" + caveats_md +
                f"\n---\n_{n_resolved}/{len(plan)} sub-questions resolved from "
                f"{len(all_notes)} grounded notes; {len(citations)} sources cited._\n")

    env = {"job_id": job_id, "document": document, "plan": plan,
           "citations": citations}
    env["trace"] = tr.finish(outcome="resolved", n_sub_questions=len(plan),
                             n_resolved=n_resolved, n_citations=len(citations))
    return env
