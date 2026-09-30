"""
Outbound-fetch guard for URLs the model, a user or a third-party record chose.

web_fetch, source({url}) and the OA-PDF downloaders fetch addresses that
arrive from chat text, fetched pages or Unpaywall/S2 metadata. Unguarded, a
prompt-injected page could make the retrieval container read its own
neighbours (qdrant:6333, neo4j:7474, the sandbox, vLLM on the host) and hand
the response back to the chat. `guarded_client()` returns an httpx client whose
transport refuses, on every request including each redirect hop, any scheme
but http/https and any host that resolves to a non-public address.

Residual: the check resolves the name, then httpx resolves it again to
connect, so a DNS-rebinding host with a near-zero TTL could still race it.
Closing that needs connecting to the checked IP itself.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from typing import Any

import httpx

# Bodies beyond this are cut off, not failed: an HTML page this size is a
# data dump, and trafilatura on its first MB still finds the article if any.
MAX_FETCH_BYTES = 10 * 1024 * 1024
# httpx's timeout applies per read, so a server trickling bytes could hold a
# fetch open indefinitely; this bounds the whole body read.
MAX_READ_SECONDS = 60.0


class BlockedURL(httpx.RequestError):
    """The URL points somewhere the retrieval service must not fetch."""


def _is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return ip.is_global and not ip.is_multicast


async def assert_public_url(url: httpx.URL) -> None:
    """Raise BlockedURL unless `url` is http(s) and every address its host
    resolves to is public."""
    if url.scheme not in ("http", "https"):
        raise BlockedURL(f"scheme not allowed: {url.scheme!r}")
    host = url.host
    if not host:
        raise BlockedURL("URL has no host")
    port = url.port or (443 if url.scheme == "https" else 80)
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise BlockedURL(f"cannot resolve host {host!r}: {exc}") from exc
    addrs = {info[4][0] for info in infos}
    if not addrs:
        raise BlockedURL(f"cannot resolve host {host!r}")
    for addr in addrs:
        ip = ipaddress.ip_address(addr.split("%", 1)[0])  # drop an IPv6 zone id
        if not _is_public(ip):
            raise BlockedURL(f"host {host!r} resolves to non-public address {ip}")


class GuardedTransport(httpx.AsyncBaseTransport):
    """Checks each outgoing request before it is sent. httpx sends every
    redirect hop through the transport, so a public URL that 302s to an
    internal one is refused at the hop."""

    def __init__(self, inner: httpx.AsyncBaseTransport | None = None,
                 **kwargs: Any) -> None:
        self._inner = inner or httpx.AsyncHTTPTransport(**kwargs)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await assert_public_url(request.url)
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


def guarded_client(**kwargs: Any) -> httpx.AsyncClient:
    """httpx.AsyncClient for fetching untrusted URLs; takes the usual kwargs."""
    return httpx.AsyncClient(transport=GuardedTransport(), **kwargs)


async def read_capped(response: httpx.Response,
                      max_bytes: int = MAX_FETCH_BYTES,
                      max_seconds: float = MAX_READ_SECONDS) -> bytes:
    """Read a streamed response body, stopping at `max_bytes` or once
    `max_seconds` have passed since the read began."""
    deadline = time.monotonic() + max_seconds
    buf = bytearray()
    async for chunk in response.aiter_bytes():
        buf.extend(chunk)
        if len(buf) >= max_bytes or time.monotonic() > deadline:
            break
    return bytes(buf[:max_bytes])


async def read_text_capped(response: httpx.Response, **kwargs: Any) -> str:
    """read_capped, decoded with the response's declared charset (utf-8 if
    none); undecodable bytes, such as a multi-byte character cut at the cap,
    are replaced rather than raised."""
    data = await read_capped(response, **kwargs)
    try:
        return data.decode(response.charset_encoding or "utf-8", errors="replace")
    except LookupError:  # unknown charset name in the header
        return data.decode("utf-8", errors="replace")
