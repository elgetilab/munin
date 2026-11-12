"""Gateway-token header for eval clients that call retrieval directly.

With MUNIN_GATEWAY_TOKEN set on retrieval, a request carrying a forwarded
identity header (X-Munin-Email, ...) must also carry X-Munin-Gateway-Token or
it gets a 401 (backend/retrieval/gateway_token_guard.py). The eval runners
call retrieval directly with an identity header, so they send the token too.

The token comes from MUNIN_GATEWAY_TOKEN, or from the file named by
MUNIN_GATEWAY_TOKEN_FILE (keeps it out of the shell history). Neither set:
no header, which is what a retrieval without the guard expects.
"""

from __future__ import annotations

import os


def gateway_headers() -> dict[str, str]:
    """{"X-Munin-Gateway-Token": token} when a token is configured, else {}.

    A MUNIN_GATEWAY_TOKEN_FILE that cannot be read raises, so a run that was
    meant to authenticate fails loudly instead of collecting 401s.
    """
    token = os.environ.get("MUNIN_GATEWAY_TOKEN", "").strip()
    path = os.environ.get("MUNIN_GATEWAY_TOKEN_FILE", "").strip()
    if not token and path:
        with open(path, encoding="utf-8") as fh:
            token = fh.read().strip()
    return {"X-Munin-Gateway-Token": token} if token else {}
