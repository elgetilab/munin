"""Which user a request is for, as the key their data is stored under.

munin-auth gives every account a user key, "u" plus its id, and the gateway
forwards it as X-Munin-User. Every per-user column here (the ones named
user_email, which predate keys), the user_docs directory hash and the Qdrant
user_docs payload hold this value. Callers that predate user keys (scripts,
benchmarks, tests) send only X-Munin-Email; their address is their key. The
two forms cannot collide: a user key has no "@".

X-Munin-Email itself is the user's primary address, for the few places that
need an address: the admin role check and KB contribution attribution.
"""

from __future__ import annotations

from typing import Mapping, Optional


def request_user_key(headers: Mapping[str, str]) -> Optional[str]:
    key = (headers.get("X-Munin-User") or headers.get("X-Munin-Email") or "").strip().lower()
    return key or None
