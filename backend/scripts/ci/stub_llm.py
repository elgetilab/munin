#!/usr/bin/env python3
"""
==============================================================================
Minimal OpenAI-compatible stub server
==============================================================================
Answers `/v1/models` and `/v1/chat/completions` with fixed content. It exists so
the stack can be booted and exercised WITHOUT a language model:

  * CI can assert that the single-host quick-start actually comes up and serves
    a turn, which is the check that stops the quick-start rotting between
    releases.
  * A developer with no GPU and no API key can bring Munin up, click around,
    and see the plumbing work.

It is not a model. Every reply is canned. Anything that depends on the answer
being sensible (retrieval quality, agent behaviour, the benchmarks) is
meaningless against it, by design.

WHAT IT ALSO CHECKS
    Munin sends `chat_template_kwargs`, which is vLLM's non-OpenAI passthrough
    for switching off reasoning. --strict makes the stub reject unknown
    top-level fields with a 400, exactly as a strict server would. That turns
    "does LLM_THINKING_TOGGLE actually work" into a test rather than a claim:
    with the toggle on, a strict stub must 400; with it off, the same request
    must succeed.

USAGE
    stub_llm.py [--port 8000] [--model NAME] [--strict] [--reply TEXT]
==============================================================================
"""

from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Fields a plain OpenAI-compatible server accepts. Anything else is what
# --strict rejects. Deliberately generous: the point is to catch vendor
# extensions like chat_template_kwargs, not to police every optional field.
OPENAI_FIELDS = {
    "model", "messages", "max_tokens", "max_completion_tokens", "temperature",
    "top_p", "n", "stream", "stream_options", "stop", "presence_penalty",
    "frequency_penalty", "logit_bias", "logprobs", "top_logprobs", "user",
    "seed", "tools", "tool_choice", "parallel_tool_calls", "response_format",
}

ARGS = None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):          # quieter CI logs
        pass

    def _send(self, code: int, payload: dict):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        """With --api-key, behave like a hosted API: 401 without the key."""
        if not ARGS.api_key:
            return True
        if self.headers.get("Authorization") == f"Bearer {ARGS.api_key}":
            return True
        self._send(401, {"error": {"message": "invalid api key"}})
        self.close_connection = True
        return False

    def do_GET(self):
        if not self._authorized():
            return
        if self.path.rstrip("/") in ("/v1/models", "/models"):
            self._send(200, {"object": "list", "data": [{
                "id": ARGS.model, "object": "model", "owned_by": "stub",
                "max_model_len": ARGS.max_model_len,
            }]})
        elif self.path.rstrip("/") in ("/health", "/v1/health"):
            self._send(200, {"status": "ok"})
        else:
            self._send(404, {"error": {"message": f"no route {self.path}"}})

    def do_POST(self):
        if not self._authorized():
            return
        if self.path.rstrip("/") not in ("/v1/chat/completions", "/chat/completions"):
            self._send(404, {"error": {"message": f"no route {self.path}"}})
            return

        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError as e:
            self._send(400, {"error": {"message": f"bad JSON: {e}"}})
            return

        if ARGS.strict:
            unknown = sorted(set(body) - OPENAI_FIELDS)
            if unknown:
                # Mirrors how a strict server rejects vendor extensions.
                self._send(400, {"error": {
                    "message": f"Unrecognized request argument supplied: "
                               f"{', '.join(unknown)}",
                    "type": "invalid_request_error",
                }})
                return

        if body.get("stream"):
            self._send_stream()
            return

        self._send(200, {
            "id": "chatcmpl-stub", "object": "chat.completion",
            "created": int(time.time()), "model": body.get("model", ARGS.model),
            "choices": [{
                "index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": ARGS.reply},
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    def _send_stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        def frame(delta: dict, finish=None):
            chunk = {
                "id": "chatcmpl-stub", "object": "chat.completion.chunk",
                "created": int(time.time()), "model": ARGS.model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
            self.wfile.flush()

        frame({"role": "assistant", "content": ""})
        frame({"content": ARGS.reply})
        frame({}, finish="stop")
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        self.close_connection = True


def main() -> int:
    global ARGS
    ap = argparse.ArgumentParser(description="Minimal OpenAI-compatible stub.")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--model", default="qwen3.6-35b-a3b")
    ap.add_argument("--max-model-len", type=int, default=65536)
    ap.add_argument("--reply", default="This is a stub response.")
    ap.add_argument("--api-key", default="",
                    help="require 'Authorization: Bearer <key>', like a hosted API")
    ap.add_argument("--strict", action="store_true",
                    help="reject non-OpenAI request fields with 400")
    ARGS = ap.parse_args()

    srv = ThreadingHTTPServer((ARGS.host, ARGS.port), Handler)
    print(f"[stub-llm] serving {ARGS.model} on {ARGS.host}:{ARGS.port} "
          f"(strict={ARGS.strict})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
