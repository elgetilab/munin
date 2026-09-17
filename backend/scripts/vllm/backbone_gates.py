#!/usr/bin/env python3
"""Backbone gates: is a served vLLM + retrieval pair fit to be measured?

Mechanises the step-1 list of docs/paper-track/done/MODEL-SWAP-QWEN38-PLAN.md
so a backbone change (production `deploy.sh model activate`, or a secondary
`deploy.sh instance up`) is checked the same way every time, and the numbers
that used to be read out of logs by hand are written to a JSON file the
benchmark scorecards can carry as provenance.

    backbone_gates.py --vllm http://127.0.0.1:8001 --api http://127.0.0.1:8082 \
        --model gpt-oss-20b --thinking-mode effort_low --reasoning-effort medium \
        --max-num-seqs 2 --max-model-len 65536 \
        --job-out /opt/munin/logs/vllm-inst-eval-1234.out \
        --tokenizer /opt/munin/data/models/tokenizer-gpt-oss-20b/tokenizer.json \
        --checkpoint /opt/munin/data/models/gpt-oss-20b \
        --out /opt/munin/instances/eval/gates.json

Every gate prints `[PASS]`/`[FAIL]`/`[WARN]`/`[SKIP]` with the number it
measured next to the threshold it compared against. Exit 1 on any FAIL.
Stdlib only, so it runs anywhere python3 does (root or operator, host or
container). It never tunes anything.

Gates:
  vllm_health          /health 200
  served_model         /v1/models lists --model with max_model_len == --max-model-len
  kv_pool              `GPU KV cache size` in --job-out >= max_num_seqs x max_model_len
                       (also records `Maximum concurrency` and `Mxfp4 MoE backend`)
  tokenizer            sha256(--tokenizer) == sha256(<checkpoint>/tokenizer.json)
  completion           a plain chat completion returns content
  reasoning_effort     chat_template_kwargs.reasoning_effort accepted (200)
  thinking_off         completion tokens WITH the thinking-off fields < WITHOUT
                       (ratio recorded; < 0.5 expected for enable_thinking)
  api_health           --api /health 200
  api_models           --api /api/models id == --model and served == true
  tool_call            one research-persona turn through --api yields >= 1
                       tool_call event and no raw tool markup in the answer
  decode_tps           single-stream decode tokens/s (recorded, not gated)
  prefill_tps          ~20k-token prompt prefill tokens/s (recorded, not gated)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

RESULTS: dict[str, dict] = {}
FAILED = False

_TOOL_MARKUP = ("<tool_call>", "</tool_call>", "<|channel|>", "<|call|>", "<|start|>",
                "to=functions.")


def _report(name: str, status: str, detail: str, **data) -> None:
    global FAILED
    if status == "FAIL":
        FAILED = True
    RESULTS[name] = {"status": status, "detail": detail, **data}
    print(f"[{status:4s}] {name}: {detail}", flush=True)


def _get(url: str, timeout: float = 10.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, json.load(r)


def _post_json(url: str, body: dict, timeout: float = 300.0, headers: dict | None = None):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **(headers or {})}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            payload = {}
        return e.code, payload


def thinking_off_fields(mode: str) -> dict:
    if mode == "enable_thinking":
        return {"chat_template_kwargs": {"enable_thinking": False}}
    if mode == "effort_low":
        return {"chat_template_kwargs": {"reasoning_effort": "low"}}
    if mode == "none":
        return {}
    raise SystemExit(f"unknown --thinking-mode {mode!r}")


# --- vLLM-side gates -----------------------------------------------------------

def gate_vllm_health(vllm: str) -> bool:
    try:
        with urllib.request.urlopen(vllm + "/health", timeout=10) as r:
            ok = r.status == 200
    except Exception as e:
        _report("vllm_health", "FAIL", f"{vllm}/health unreachable: {e}")
        return False
    _report("vllm_health", "PASS" if ok else "FAIL", f"{vllm}/health -> {200 if ok else 'not 200'}")
    return ok


def gate_served_model(vllm: str, model: str, max_model_len: int) -> None:
    try:
        _, d = _get(vllm + "/v1/models")
    except Exception as e:
        _report("served_model", "FAIL", f"/v1/models unreachable: {e}")
        return
    ids = {m.get("id"): m for m in d.get("data", [])}
    if model not in ids:
        _report("served_model", "FAIL", f"served {sorted(ids)}, expected {model!r}")
        return
    mml = ids[model].get("max_model_len")
    root = ids[model].get("root")
    if mml != max_model_len:
        _report("served_model", "FAIL", f"{model} max_model_len={mml}, expected {max_model_len}",
                model_path=root, max_model_len=mml)
        return
    _report("served_model", "PASS", f"{model} max_model_len={mml} root={root}",
            model_path=root, max_model_len=mml)


def gate_kv_pool(job_out: str | None, max_num_seqs: int, max_model_len: int) -> None:
    if not job_out:
        _report("kv_pool", "SKIP", "no --job-out given")
        return
    if not os.path.exists(job_out):
        _report("kv_pool", "FAIL", f"{job_out} not found")
        return
    text = open(job_out, errors="replace").read()
    m = re.search(r"GPU KV cache size:\s*([\d,]+)\s*tokens", text)
    conc = re.search(r"Maximum concurrency for [\d,]+ tokens per request:\s*([\d.]+)x", text)
    moe = re.search(r"Using '([^']+)' Mxfp4 MoE backend", text)
    if not m:
        _report("kv_pool", "FAIL", "no 'GPU KV cache size' line yet in the job log")
        return
    tokens = int(m.group(1).replace(",", ""))
    need = max_num_seqs * max_model_len
    status = "PASS" if tokens >= need else "FAIL"
    _report("kv_pool", status,
            f"KV pool {tokens:,} tokens vs {max_num_seqs} x {max_model_len:,} = {need:,} needed"
            + (f"; max concurrency {conc.group(1)}x" if conc else "")
            + (f"; MoE backend {moe.group(1)}" if moe else ""),
            kv_tokens=tokens, kv_tokens_needed=need,
            max_concurrency_at_window=float(conc.group(1)) if conc else None,
            moe_backend=moe.group(1) if moe else None)


def gate_tokenizer(staged: str | None, checkpoint: str | None) -> None:
    if not staged or not checkpoint:
        _report("tokenizer", "SKIP", "no --tokenizer/--checkpoint given")
        return
    src = os.path.join(checkpoint, "tokenizer.json")
    if not os.path.exists(staged) or not os.path.exists(src):
        _report("tokenizer", "FAIL", f"missing: staged={os.path.exists(staged)} checkpoint={os.path.exists(src)}")
        return
    a = hashlib.sha256(open(staged, "rb").read()).hexdigest()
    b = hashlib.sha256(open(src, "rb").read()).hexdigest()
    _report("tokenizer", "PASS" if a == b else "FAIL",
            f"staged {a[:12]} vs checkpoint {b[:12]}", staged_sha256=a, checkpoint_sha256=b)


def _chat(vllm: str, model: str, prompt: str, *, max_tokens: int, extra: dict | None = None,
          system: str | None = None, timeout: float = 300.0):
    msgs = ([{"role": "system", "content": system}] if system else []) + \
           [{"role": "user", "content": prompt}]
    body = {"model": model, "messages": msgs, "max_tokens": max_tokens,
            "temperature": 0.0, "stream": False, **(extra or {})}
    t0 = time.time()
    code, d = _post_json(vllm + "/v1/chat/completions", body, timeout=timeout)
    return code, d, time.time() - t0


def gate_completion(vllm: str, model: str, mode: str = "none") -> None:
    # Thinking off (in the profile's form) and a real budget: with reasoning
    # on and 64 tokens, Qwen3.6 spent the whole budget inside <think> and
    # returned empty content, failing a gate that was meant to catch a dead
    # server, not a chatty model (first Qwen3.6 instance start, 2026-09-17).
    code, d, dt = _chat(vllm, model, "Reply with the single word: ready", max_tokens=512,
                        extra=thinking_off_fields(mode))
    content = ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    if code != 200:
        _report("completion", "FAIL", f"HTTP {code}: {json.dumps(d)[:200]}")
        return
    _report("completion", "PASS" if content.strip() else "FAIL",
            f"HTTP 200, content={content.strip()[:40]!r} in {dt:.1f}s")


def gate_reasoning_effort(vllm: str, model: str, effort: str) -> None:
    if not effort:
        _report("reasoning_effort", "SKIP", "profile sends no reasoning_effort")
        return
    code, d, _ = _chat(vllm, model, "Reply with the single word: ready", max_tokens=64,
                       extra={"chat_template_kwargs": {"reasoning_effort": effort}})
    if code == 200:
        _report("reasoning_effort", "PASS", f"chat_template_kwargs.reasoning_effort={effort!r} accepted")
    else:
        _report("reasoning_effort", "FAIL",
                f"HTTP {code} with reasoning_effort={effort!r}: blank LLM_REASONING_EFFORT in the profile. "
                f"{json.dumps(d)[:160]}")


_SUMMARISE_SYSTEM = ("You summarise text. Reply with the summary only, one paragraph, "
                     "no preamble.")
_SUMMARISE_TEXT = (
    "Graph neural networks operate on graph-structured data by iteratively passing messages "
    "between neighbouring nodes and aggregating them into node representations. Applied to "
    "molecules, atoms become nodes and bonds become edges, and the learned representations "
    "predict properties such as solubility, toxicity or binding affinity. Recent work adds "
    "geometric information, three-dimensional coordinates and rotational equivariance, which "
    "improves accuracy on quantum-chemical targets at the cost of heavier models. A persistent "
    "difficulty is generalisation to scaffolds absent from the training set, where message "
    "passing over-smooths and distinct molecules collapse to similar embeddings. "
) * 3


_REASONING_PROMPT = (
    "A lab has 3 sequencers. Each run takes 6 hours and processes 48 samples. "
    "Runs cannot overlap on one machine and each machine needs 1 hour of cleaning "
    "between runs. How many samples can the lab process in 5 days of continuous "
    "operation? Reply with just the number.")


def gate_thinking_off(vllm: str, model: str, mode: str) -> None:
    """Does the profile's thinking-off form actually shorten generation?

    enable_thinking: an off-switch; compared against the model default on a
    summarise prompt, expected < 0.5x, FAIL if not shorter at all.
    effort_low: a dial, not a switch. Compared against reasoning_effort=high
    on a reasoning-heavy prompt, where the dial has something to shorten; a
    summarise prompt at low vs medium is noise (measured 200 vs 354, then 292
    vs 278 on the same model). WARN, not FAIL, if it does not shorten: the
    model still answers, the cost column just gets reported as-is.
    """
    fields = thinking_off_fields(mode)
    if not fields:
        _report("thinking_off", "SKIP", f"mode={mode}: nothing to send")
        return
    if mode == "effort_low":
        prompt, system = _REASONING_PROMPT, None
        baseline = {"chat_template_kwargs": {"reasoning_effort": "high"}}
        hard_fail = False
    else:
        prompt, system = "Summarise the following in two sentences:\n\n" + _SUMMARISE_TEXT, _SUMMARISE_SYSTEM
        baseline = None
        hard_fail = True
    code_on, d_on, dt_on = _chat(vllm, model, prompt, max_tokens=8192, system=system, extra=baseline)
    code_off, d_off, dt_off = _chat(vllm, model, prompt, max_tokens=8192, system=system, extra=fields)
    if code_on != 200 or code_off != 200:
        _report("thinking_off", "FAIL", f"HTTP on={code_on} off={code_off}: {json.dumps(d_off)[:160]}")
        return
    on = (d_on.get("usage") or {}).get("completion_tokens")
    off = (d_off.get("usage") or {}).get("completion_tokens")
    if not on or off is None:
        _report("thinking_off", "FAIL", f"no usage in response (on={on} off={off})")
        return
    ratio = off / on if on else None
    vs = json.dumps(baseline["chat_template_kwargs"]) if baseline else "model default"
    if off < on:
        status = "PASS" if ratio < 0.5 else "WARN"
        detail = (f"completion tokens with {json.dumps(fields['chat_template_kwargs'])}: {off} "
                  f"vs {vs}: {on} (ratio {ratio:.2f}"
                  + ("" if ratio < 0.5 else ", expected < 0.5 for a real off-switch") + ")")
    else:
        status = "FAIL" if hard_fail else "WARN"
        detail = (f"thinking-off fields did not reduce output: {off} vs {on} tokens against {vs} "
                  f"(mode {mode}{' inert?' if hard_fail else ': a dial, not a switch; cost reported as-is'})")
    _report("thinking_off", status, detail, tokens_with_fields=off, tokens_without=on,
            ratio=round(ratio, 3) if ratio is not None else None, mode=mode)


def gate_decode_tps(vllm: str, model: str, mode: str) -> None:
    # 300 tokens of plain text at thinking off (or effort low), single stream.
    fields = thinking_off_fields(mode)
    code, d, dt = _chat(vllm, model,
                        "Write a continuous plain-prose description of how a river forms, "
                        "at least 400 words, no lists.", max_tokens=300, extra=fields)
    if code != 200:
        _report("decode_tps", "WARN", f"HTTP {code}; not measured")
        return
    n = (d.get("usage") or {}).get("completion_tokens") or 0
    tps = n / dt if dt else 0.0
    _report("decode_tps", "PASS", f"{n} tokens in {dt:.1f}s = {tps:.1f} tok/s (single stream, recorded)",
            completion_tokens=n, seconds=round(dt, 2), tokens_per_s=round(tps, 1))


def gate_prefill_tps(vllm: str, model: str, mode: str) -> None:
    sentence = "The quick brown fox jumps over the lazy dog near the riverbank. "
    prompt = "Ignore the filler and reply with the single word: done.\n\n" + sentence * 1250
    fields = thinking_off_fields(mode)
    code, d, dt = _chat(vllm, model, prompt, max_tokens=8, extra=fields)
    if code != 200:
        _report("prefill_tps", "WARN", f"HTTP {code}; not measured: {json.dumps(d)[:120]}")
        return
    n = (d.get("usage") or {}).get("prompt_tokens") or 0
    tps = n / dt if dt else 0.0
    _report("prefill_tps", "PASS", f"{n:,} prompt tokens in {dt:.1f}s = {tps:,.0f} tok/s (recorded)",
            prompt_tokens=n, seconds=round(dt, 2), tokens_per_s=round(tps, 1))


# --- retrieval-side gates ------------------------------------------------------

def gate_api_health(api: str) -> bool:
    try:
        with urllib.request.urlopen(api + "/health", timeout=10) as r:
            ok = r.status == 200
    except Exception as e:
        _report("api_health", "FAIL", f"{api}/health unreachable: {e}")
        return False
    _report("api_health", "PASS" if ok else "FAIL", f"{api}/health -> {200 if ok else 'not 200'}")
    return ok


def gate_api_models(api: str, model: str) -> None:
    try:
        _, d = _get(api + "/api/models")
    except Exception as e:
        _report("api_models", "FAIL", f"/api/models unreachable ({e}); pre-2026-09-15 image?")
        return
    entry = (d.get("data") or [{}])[0]
    if entry.get("id") != model:
        _report("api_models", "FAIL", f"/api/models id={entry.get('id')!r}, expected {model!r}", **{
            k: entry.get(k) for k in ("served", "thinking_mode", "reasoning_effort")})
        return
    served = bool(entry.get("served"))
    _report("api_models", "PASS" if served else "FAIL",
            f"id={model} served={served} thinking_mode={entry.get('thinking_mode')} "
            f"effort={entry.get('reasoning_effort')} window={entry.get('context_window')}",
            served=served, thinking_mode=entry.get("thinking_mode"),
            reasoning_effort=entry.get("reasoning_effort"), context_window=entry.get("context_window"))


def gate_tool_call(api: str, email: str, deadline: int = 420) -> None:
    body = {"persona": "research", "ephemeral": True,
            "messages": [{"role": "user", "content":
                          "Using the paper corpus only, find two papers about graph neural "
                          "networks for molecular property prediction and give their titles "
                          "and years. Do not use web search."}]}
    req = urllib.request.Request(
        api.rstrip("/") + "/api/chat/completions", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-Munin-Email": email,
                 "X-Munin-Ephemeral": "true", "Accept": "text/event-stream",
                 "X-Munin-Egress": "off"}, method="POST")
    content, n_calls, n_errors, tools = "", 0, 0, []
    t0 = time.time()
    try:
        resp = urllib.request.urlopen(req, timeout=60)
    except Exception as e:
        _report("tool_call", "FAIL", f"chat request failed: {e}")
        return
    ev = None
    try:
        for raw in resp:
            if time.time() - t0 > deadline:
                break
            line = raw.decode("utf-8", "replace").rstrip("\n")
            if line.startswith("event:"):
                ev = line[6:].strip(); continue
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                o = json.loads(payload)
            except Exception:
                continue
            if ev == "token" and o.get("content"):
                content += o["content"]
            elif ev == "tool_call":
                n_calls += 1
                tools.append(o.get("name"))
            elif ev == "tool_result":
                res = o.get("result")
                if isinstance(res, dict) and "error" in res:
                    n_errors += 1
            elif ev == "done":
                break
    finally:
        resp.close()
    leaks = [m for m in _TOOL_MARKUP if m in content]
    dt = time.time() - t0
    if n_calls == 0:
        _report("tool_call", "FAIL", f"0 tool_call events in {dt:.0f}s; answer={content[:120]!r} "
                                     f"(wrong --tool-call-parser, or parse leak: {leaks})",
                tool_calls=0, tool_errors=n_errors, markup_leaks=leaks, seconds=round(dt, 1))
        return
    if leaks:
        _report("tool_call", "FAIL", f"{n_calls} tool calls but raw markup in content: {leaks}",
                tool_calls=n_calls, tool_errors=n_errors, markup_leaks=leaks, tools=tools)
        return
    _report("tool_call", "PASS", f"{n_calls} tool calls ({', '.join(t for t in tools if t)}), "
                                 f"{n_errors} tool errors, {len(content)} chars in {dt:.0f}s",
            tool_calls=n_calls, tool_errors=n_errors, tools=tools, seconds=round(dt, 1),
            answer_chars=len(content))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vllm", required=True, help="vLLM base URL, e.g. http://127.0.0.1:8001")
    ap.add_argument("--api", default=None, help="retrieval base URL, e.g. http://127.0.0.1:8082 (omit to skip API gates)")
    ap.add_argument("--model", required=True, help="served model name")
    ap.add_argument("--thinking-mode", default="enable_thinking", choices=["enable_thinking", "effort_low", "none"])
    ap.add_argument("--reasoning-effort", default="", help="blank = the profile sends none")
    ap.add_argument("--max-num-seqs", type=int, default=2)
    ap.add_argument("--max-model-len", type=int, default=65536)
    ap.add_argument("--job-out", default=None, help="vLLM SLURM job .out for the KV pool line")
    ap.add_argument("--tokenizer", default=None, help="staged tokenizer.json")
    ap.add_argument("--checkpoint", default=None, help="checkpoint dir holding the reference tokenizer.json")
    ap.add_argument("--email", default="backbone-gates@localhost")
    ap.add_argument("--skip", default="", help="comma-separated gate names to skip (e.g. tool_call,prefill_tps)")
    ap.add_argument("--out", default=None, help="write the gate results as JSON here")
    args = ap.parse_args()
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}

    def want(name: str) -> bool:
        if name in skip:
            _report(name, "SKIP", "skipped by --skip")
            return False
        return True

    print(f"== backbone gates: {args.model} @ {args.vllm}" + (f" / {args.api}" if args.api else ""))
    if want("vllm_health") and gate_vllm_health(args.vllm):
        if want("served_model"):
            gate_served_model(args.vllm, args.model, args.max_model_len)
        if want("completion"):
            gate_completion(args.vllm, args.model, args.thinking_mode)
        if want("reasoning_effort"):
            gate_reasoning_effort(args.vllm, args.model, args.reasoning_effort)
        if want("thinking_off"):
            gate_thinking_off(args.vllm, args.model, args.thinking_mode)
        if want("decode_tps"):
            gate_decode_tps(args.vllm, args.model, args.thinking_mode)
        if want("prefill_tps"):
            gate_prefill_tps(args.vllm, args.model, args.thinking_mode)
    if want("kv_pool"):
        gate_kv_pool(args.job_out, args.max_num_seqs, args.max_model_len)
    if want("tokenizer"):
        gate_tokenizer(args.tokenizer, args.checkpoint)
    if args.api:
        if want("api_health") and gate_api_health(args.api):
            if want("api_models"):
                gate_api_models(args.api, args.model)
            if want("tool_call"):
                gate_tool_call(args.api, args.email)
    summary = {"model": args.model, "vllm": args.vllm, "api": args.api,
               "thinking_mode": args.thinking_mode, "reasoning_effort": args.reasoning_effort or None,
               "max_num_seqs": args.max_num_seqs, "max_model_len": args.max_model_len,
               "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "overall": "FAIL" if FAILED else "PASS", "gates": RESULTS}
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w") as fh:
            json.dump(summary, fh, indent=2)
        print(f"-> {args.out}")
    print(f"== OVERALL: {summary['overall']}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
