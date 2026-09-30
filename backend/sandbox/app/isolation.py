"""
Per-conversation isolation for user code (kernels and LaTeX).

Why this exists: firejail, which the sandbox used before 2026-09-30, detects
that it is inside a container and runs the command with NO sandboxing, while
the service reported `firejail: true`. Kernels ran as root, could read and
write every conversation's files, call the sandbox API on loopback (and so
run code in other users' kernels) and reach the retrieval service.

Now every piece of user code runs as `unshare --net -- python jail.py --uid U
-- ...`: its own network namespace (loopback only) and a uid from a pool that
no other live conversation holds. Scratch dirs are 0700 per uid under a 0711
root, artifact manifests live in META_ROOT where kernels cannot write, and the
root service opens kernel-written files without following symlinks.

`self_test()` measures all of that at startup by running a probe through the
same launcher; the service refuses to run code when it fails, so isolation
cannot silently disappear again.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import stat
import sys
from typing import Optional

logger = logging.getLogger("sandbox-svc")

SCRATCH_ROOT = os.environ.get("SANDBOX_SCRATCH_DIR", "/scratch")
META_ROOT = os.environ.get("SANDBOX_META_DIR", "/scratch-meta")
RUNTIME_ROOT = os.environ.get("SANDBOX_RUNTIME_DIR", "/run/munin-sandbox")
UID_BASE = int(os.environ.get("SANDBOX_UID_BASE", "20000"))
UID_COUNT = int(os.environ.get("SANDBOX_UID_COUNT", "128"))
SERVICE_PORT = int(os.environ.get("SANDBOX_PORT", "8090"))

JAIL_PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jail.py")

# Resource caps applied by jail.py before exec. RLIMIT_NPROC counts threads of
# the uid, and a kernel alone runs ~10; BLAS pools are pinned to 1 thread.
KERNEL_LIMITS = {
    "as": 2 * 1024 * 1024 * 1024,
    "fsize": 100 * 1024 * 1024,
    "nofile": 256,
    "nproc": 256,
}
LATEX_LIMITS = dict(KERNEL_LIMITS, fsize=50 * 1024 * 1024)

# Environment handed to user code: an allowlist, so nothing from the
# service's own environment leaks into it.
_ENV_PASSTHROUGH = ("PATH", "LANG", "LC_ALL", "MPLBACKEND", "PYTHONUNBUFFERED",
                    "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
                    "NUMEXPR_NUM_THREADS")


class SandboxBusy(RuntimeError):
    """Every uid in the pool is held by a live conversation."""


def valid_conversation_id(cid: str) -> bool:
    """Conversation ids become directory names that are chown'd recursively,
    so only the shapes retrieval sends (uuids and similar) are accepted."""
    return (isinstance(cid, str) and 0 < len(cid) <= 64
            and cid.replace("-", "").isalnum() and cid.strip("-") != "")


def scratch_dir(cid: str) -> str:
    return os.path.join(SCRATCH_ROOT, cid)


def meta_dir(cid: str) -> str:
    d = os.path.join(META_ROOT, cid)
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def ensure_roots() -> None:
    """Root-owned parents: /scratch can be traversed but not listed, and the
    manifest and runtime roots are closed to kernels."""
    for path, mode in ((SCRATCH_ROOT, 0o711), (META_ROOT, 0o700), (RUNTIME_ROOT, 0o711)):
        os.makedirs(path, exist_ok=True)
        os.chown(path, 0, 0)
        os.chmod(path, mode)


def jail_prefix(uid: int, limits: dict) -> list[str]:
    return ["unshare", "--net", "--", sys.executable, JAIL_PY, "--uid", str(uid),
            "--rlimit-as", str(limits["as"]), "--rlimit-fsize", str(limits["fsize"]),
            "--rlimit-nofile", str(limits["nofile"]), "--rlimit-nproc", str(limits["nproc"]),
            "--"]


def user_env(home: str) -> dict[str, str]:
    env = {k: os.environ[k] for k in _ENV_PASSTHROUGH if k in os.environ}
    env.update(HOME=home, MPLCONFIGDIR=os.path.join(home, "mpl"),
               IPYTHONDIR=os.path.join(home, "ipython"), TMPDIR=os.path.join(home, "tmp"),
               JUPYTER_RUNTIME_DIR=home)
    return env


def chown_tree(path: str, uid: int) -> None:
    """Give a conversation's dir to its current uid. Never follows symlinks,
    so a link planted in the dir cannot redirect the chown."""
    os.chown(path, uid, uid, follow_symlinks=False)
    os.chmod(path, 0o700)
    for root, dirs, files in os.walk(path, followlinks=False):
        for name in dirs + files:
            try:
                os.chown(os.path.join(root, name), uid, uid, follow_symlinks=False)
            except OSError:
                pass


def fresh_runtime_dir(uid: int) -> str:
    """Per-uid home + runtime dir, recreated empty for each lease."""
    import shutil
    d = os.path.join(RUNTIME_ROOT, str(uid))
    shutil.rmtree(d, ignore_errors=True)
    for sub in ("", "mpl", "ipython", "tmp"):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
    chown_tree(d, uid)
    return d


def kill_uid(uid: int) -> int:
    """SIGKILL every process running as `uid`. A kernel can leave background
    processes behind, which must not outlive its lease: the uid goes to the
    next conversation."""
    killed = 0
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/status") as f:
                uids = next((l.split()[1:] for l in f if l.startswith("Uid:")), [])
        except OSError:
            continue
        if str(uid) in uids:
            try:
                os.kill(int(pid), signal.SIGKILL)
                killed += 1
            except ProcessLookupError:
                pass
    return killed


class UidPool:
    """Leases a uid per conversation. Kernel and LaTeX runs of the same
    conversation share one uid (refcounted), and no two live conversations
    ever share one. On the last release every process of the uid is killed."""

    def __init__(self, base: int = UID_BASE, count: int = UID_COUNT) -> None:
        self._free = list(range(base, base + count))
        self._held: dict[str, list[int]] = {}  # cid -> [uid, refcount]
        self._lock = asyncio.Lock()

    async def acquire(self, cid: str) -> int:
        async with self._lock:
            held = self._held.get(cid)
            if held:
                held[1] += 1
                return held[0]
            if not self._free:
                raise SandboxBusy("sandbox is at its concurrent-conversation limit")
            uid = self._free.pop(0)
            kill_uid(uid)  # nothing may survive from a previous lease
            os.makedirs(scratch_dir(cid), exist_ok=True)
            chown_tree(scratch_dir(cid), uid)
            self._held[cid] = [uid, 1]
            return uid

    async def release(self, cid: str) -> None:
        async with self._lock:
            held = self._held.get(cid)
            if not held:
                return
            held[1] -= 1
            if held[1] > 0:
                return
            del self._held[cid]
            kill_uid(held[0])
            self._free.append(held[0])


pool = UidPool()


# ----------------------------------------------------------------------------
# Opening kernel-written files as root
# ----------------------------------------------------------------------------

def open_regular_nofollow(dir_path: str, name: str) -> Optional[int]:
    """fd of `name` in `dir_path` if it is a regular file, else None. The
    kernel owns the dir, so the name may be a symlink to anything; O_NOFOLLOW
    refuses that, and fstat on the opened fd rules out a swap after a check."""
    if not name or "/" in name or name in (".", ".."):
        return None
    try:
        dfd = os.open(dir_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dfd)
    except OSError:
        return None
    finally:
        os.close(dfd)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        return None
    return fd


def is_regular_nofollow(path: str) -> bool:
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def copy_regular_nofollow(src_dir: str, name: str, dst_path: str) -> Optional[int]:
    """Copy a regular file out of a kernel-owned dir into a fresh file the
    kernel cannot pre-plant (O_EXCL). Returns bytes copied, or None."""
    fd = open_regular_nofollow(src_dir, name)
    if fd is None:
        return None
    try:
        out = os.open(dst_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    except OSError:
        os.close(fd)
        return None
    n = 0
    with os.fdopen(fd, "rb") as src, os.fdopen(out, "wb") as dst:
        while chunk := src.read(1 << 20):
            dst.write(chunk)
            n += len(chunk)
    return n


def write_new_file(path: str, data: bytes) -> None:
    """Create a file as root where a kernel might have planted a link."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


# ----------------------------------------------------------------------------
# Startup self-test
# ----------------------------------------------------------------------------

_PROBE = r"""
import json, os, socket
out = {"uid": os.getuid()}
s = socket.socket(); s.settimeout(2)
try:
    s.connect(("127.0.0.1", PORT)); out["service_reachable"] = True
except OSError:
    out["service_reachable"] = False
try:
    os.listdir(SCRATCH); out["scratch_listable"] = True
except OSError:
    out["scratch_listable"] = False
out["app_writable"] = os.access(APP, os.W_OK)
out["meta_readable"] = os.access(META, os.R_OK)
print(json.dumps(out))
"""


async def self_test() -> dict:
    """Run a probe through the real launcher and report what it could do.
    `ok` is True only if the probe ran as non-root with no route to the
    service port and no view of other conversations or the service's code."""
    probe_uid = UID_BASE + UID_COUNT  # just past the pool, never leased
    code = (_PROBE.replace("PORT", str(SERVICE_PORT)).replace("SCRATCH", repr(SCRATCH_ROOT))
            .replace("APP", repr(JAIL_PY)).replace("META", repr(META_ROOT)))
    home = fresh_runtime_dir(probe_uid)
    result: dict = {"ok": False}
    try:
        proc = await asyncio.create_subprocess_exec(
            *jail_prefix(probe_uid, KERNEL_LIMITS), sys.executable, "-c", code,
            cwd=home, env=user_env(home),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0:
            result["error"] = err.decode(errors="replace")[-500:]
            return result
        probe = json.loads(out.decode().strip().splitlines()[-1])
        result.update(probe)
        result["ok"] = (probe["uid"] != 0 and not probe["service_reachable"]
                        and not probe["scratch_listable"] and not probe["app_writable"]
                        and not probe["meta_readable"])
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        kill_uid(probe_uid)
    return result
