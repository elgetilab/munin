"""Sandbox artifact and bundle downloads stream through the gateway instead of
being read into memory (docs/agent-track/LAB-ANALYSIS-PLAN.md, Phase 0)."""
import asyncio

import httpx


def test_download_paths(gw_env):
    m = gw_env
    assert m.is_download_path("artifacts/conv-1/9f2c")
    assert m.is_download_path("bundles/conv-1")
    assert not m.is_download_path("artifacts/conv-1")      # JSON listing
    assert not m.is_download_path("chats/conv-1/artifacts/x")


def test_stream_download_passes_bytes_and_integrity_headers(gw_env):
    m = gw_env
    body = b"\x89PNG" + b"0" * (3 * 1024 * 1024)

    class Transport(httpx.AsyncBaseTransport):
        # Not httpx.MockTransport: it reads the body before returning, and the
        # gateway streams the raw (undecoded) body like a real transport.
        async def handle_async_request(self, request):
            return httpx.Response(200, stream=httpx.ByteStream(body), headers={
                "content-type": "image/png", "x-munin-sha256": "ab" * 32,
                "etag": '"' + "ab" * 32 + '"',
                "content-disposition": 'attachment; filename="fit.png"',
                "x-internal": "dropped"})

    async def run():
        client = httpx.AsyncClient(transport=Transport(), base_url="http://b")
        resp = await m.stream_download(client, client.build_request("GET", "/api/artifacts/c/a"))
        chunks = [c async for c in resp.body_iterator]
        await client.aclose()
        return resp, b"".join(chunks)

    resp, got = asyncio.run(run())
    assert got == body
    assert resp.headers["x-munin-sha256"] == "ab" * 32
    assert resp.headers["content-disposition"] == 'attachment; filename="fit.png"'
    assert "x-internal" not in resp.headers
