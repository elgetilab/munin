"""A stand-in OpenAI server for the integration test. Standard library only,
so it runs in a bare python image with no install step.

    python stub_openai.py <port> <model-id> <label>

Answers /health, /v1/models and /v1/chat/completions (streamed or not). The
reply text is the label, so the test can tell which upstream answered. It
also records the last request body at GET /last, so the test can check what
the secondary was sent.
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT, MODEL, LABEL = int(sys.argv[1]), sys.argv[2], sys.argv[3]
LAST: dict = {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            return self._json(200, {"status": "ok"})
        if self.path == "/v1/models":
            return self._json(200, {"object": "list", "data": [{"id": MODEL, "object": "model"}]})
        if self.path == "/last":
            return self._json(200, LAST)
        return self._json(404, {"error": {"message": "not found"}})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        LAST.clear()
        LAST.update(body)
        usage = {"prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15}
        if not body.get("stream"):
            return self._json(200, {"id": "x", "object": "chat.completion", "model": MODEL,
                                    "choices": [{"index": 0, "finish_reason": "stop",
                                                 "message": {"role": "assistant", "content": LABEL}}],
                                    "usage": usage})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for piece in (LABEL[: len(LABEL) // 2], LABEL[len(LABEL) // 2:]):
            chunk = {"model": MODEL, "choices": [{"index": 0, "delta": {"content": piece}}]}
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(f"data: {json.dumps({'choices': [], 'usage': usage})}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
