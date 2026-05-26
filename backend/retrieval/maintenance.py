"""
Maintenance-mode flag reader.

Operator-triggered maintenance (distinct from the nightly 2-6 AM GPU
sleep) is signalled by a single file on the cluster,
``/opt/munin/data/maintenance.json``, written by the
``munin-maintenance`` toggle. ``/opt/munin/data`` is bind-mounted into
the retrieval container as ``/data``, so the file is readable here with
no extra mount.

``/api/status`` calls ``read_maintenance`` and includes the result as
its ``maintenance`` block; the frontend renders the maintenance page
when ``active`` is true.
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

MAINTENANCE_FLAG_PATH = os.getenv(
    "MAINTENANCE_FLAG_PATH", "/data/maintenance.json"
)


def read_maintenance(path: str | None = None) -> dict:
    """Return the maintenance block for ``/api/status``.

    - flag file absent  -> ``{"active": False}``
    - flag file present -> ``{"active": True, "message": str, "since": str}``
    - flag file present but unparseable -> still ``active: True`` with an
      empty message. Fail safe: if an operator put the file there, the
      intent was maintenance, so show it rather than swallow the state.
    """
    flag_path = path if path is not None else MAINTENANCE_FLAG_PATH
    try:
        with open(flag_path) as f:
            data = json.load(f)
    except FileNotFoundError:
        return {"active": False}
    except Exception as e:
        logger.warning("maintenance flag %s unparseable: %s", flag_path, e)
        return {"active": True, "message": "", "since": ""}
    return {
        "active": True,
        "message": str(data.get("message") or ""),
        "since": str(data.get("since") or ""),
    }
