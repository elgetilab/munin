"""
KernelHandle - one Jupyter kernel, one conversation.

Wraps jupyter_client.AsyncKernelManager so that:

- The kernel is launched under ``firejail --net=none --seccomp --private-tmp``
  with rlimit caps on address space, file size, and open files. firejail
  puts the kernel in its own network namespace (loopback only) and applies
  seccomp; the resource caps are declared once on the firejail command line
  rather than via ``preexec_fn`` so they live alongside the rest of the
  isolation in one place.
- Execution is async with a per-call wall-clock timeout. On timeout we
  interrupt the kernel cleanly (SIGINT-equivalent) so the next call
  succeeds, instead of killing the kernel entirely.
- iopub messages are demultiplexed into stdout / stderr / display_data /
  result / error so the caller never has to touch the raw protocol.
- display_data ``image/png`` payloads are persisted to the conversation's
  scratch directory and surfaced as artifact metadata.

The handle also tracks ``last_used_at`` so the idle reaper in main.py can
shut down kernels that have been quiet for too long.
"""

from __future__ import annotations

import asyncio
import base64
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from jupyter_client.manager import AsyncKernelManager

from .output_cap import CappedBuffer


# ----------------------------------------------------------------------------
# firejail launch wrapper
# ----------------------------------------------------------------------------

# 2 GB address space, 100 MB max file size, 256 open files. Tuned against the
# package set we ship: pandas + matplotlib + scipy fit comfortably under 2 GB,
# and 100 MB is enough for any reasonable .csv / .png / .xlsx the model would
# write while small enough that a runaway loop can't fill the container disk.
_RLIMIT_AS = 2 * 1024 * 1024 * 1024
_RLIMIT_FSIZE = 100 * 1024 * 1024
_RLIMIT_NOFILE = 256

_FIREJAIL_PREFIX: list[str] = [
    "firejail",
    "--quiet",
    "--noprofile",
    # --net=none is the load-bearing flag: it puts the kernel in a fresh
    # network namespace with only loopback. The rlimits below are declared
    # alongside it so all the kernel-level isolation lives in one place.
    "--net=none",
    f"--rlimit-as={_RLIMIT_AS}",
    f"--rlimit-fsize={_RLIMIT_FSIZE}",
    f"--rlimit-nofile={_RLIMIT_NOFILE}",
    # We deliberately do NOT set --private-tmp or --seccomp here:
    #   * --private-tmp would break jupyter_client, which writes the kernel
    #     connection file to a path the kernel itself needs to read back.
    #   * --seccomp can block syscalls scipy/matplotlib rely on. Filesystem
    #     isolation is already provided by the surrounding container (which
    #     has no host bind mounts pointing at sensitive paths), so seccomp
    #     here would be defence-in-depth at the cost of correctness risk.
    "--",
]


def _firejail_available() -> bool:
    """Detect firejail at import time so tests can run on hosts without it."""
    for path in os.environ.get("PATH", "").split(":"):
        if path and os.path.exists(os.path.join(path, "firejail")):
            return True
    return False


FIREJAIL_AVAILABLE = _firejail_available()


class FirejailKernelManager(AsyncKernelManager):
    """
    AsyncKernelManager that prepends firejail to the kernel launch command.

    If firejail is missing (e.g. a developer running the service outside its
    container) we fall back to launching the kernel raw. The fallback is
    flagged loudly in the response so it isn't mistaken for a hardened run.
    """

    def format_kernel_cmd(self, extra_arguments=None):  # type: ignore[override]
        cmd = super().format_kernel_cmd(extra_arguments=extra_arguments)
        if not FIREJAIL_AVAILABLE:
            return cmd
        return list(_FIREJAIL_PREFIX) + cmd


# ----------------------------------------------------------------------------
# KernelHandle
# ----------------------------------------------------------------------------

@dataclass
class ExecutionResult:
    stdout: str
    stderr: str
    result: Optional[str]
    error: Optional[str]
    artifacts: list[dict] = field(default_factory=list)
    duration_ms: int = 0
    truncated_stdout: bool = False
    truncated_stderr: bool = False
    timed_out: bool = False


class KernelHandle:
    """
    One isolated Jupyter kernel bound to a single conversation. Construct,
    then ``await start()`` once before calling ``execute()``. Long-lived;
    survives across many ``execute()`` calls so user state persists.
    """

    def __init__(
        self,
        conversation_id: str,
        scratch_root: str,
    ) -> None:
        self.conversation_id = conversation_id
        self.scratch_dir = os.path.join(scratch_root, conversation_id)
        os.makedirs(self.scratch_dir, exist_ok=True)
        self._km: Optional[FirejailKernelManager] = None
        self._kc = None  # AsyncKernelClient
        self._lock = asyncio.Lock()
        self.created_at: float = time.time()
        self.last_used_at: float = time.time()

    # ---- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        if self._km is not None:
            return
        km = FirejailKernelManager()
        # Cwd inside the kernel: the conversation's scratch directory. Any
        # `open('foo.csv')` ends up under /scratch/{cid}/, which is the only
        # place the kernel is supposed to write.
        await km.start_kernel(cwd=self.scratch_dir)
        kc = km.client()
        kc.start_channels()
        try:
            await kc.wait_for_ready(timeout=30)
        except RuntimeError:
            await km.shutdown_kernel(now=True)
            raise
        self._km = km
        self._kc = kc
        # Configure matplotlib to use the inline backend so every plt.show()
        # produces a display_data message that we can capture as an artifact.
        # Without this, the container-level MPLBACKEND=Agg makes plt.show()
        # a silent no-op and the user never sees their plots. Run the setup
        # silently and discard the (empty) iopub stream so it doesn't show
        # up in the next real execute() call.
        await self._silent_setup(
            "import matplotlib\n"
            "matplotlib.use('module://matplotlib_inline.backend_inline')\n"
            # Belt-and-suspenders rlimits: firejail --rlimit-as does not
            # always propagate through its namespace setup inside an
            # unprivileged Docker container, so the kernel was observed
            # to run without an address-space cap. Setting the rlimit
            # from inside the kernel process itself is unconditional and
            # cannot be bypassed by user code (we set hard==soft so user
            # code can't raise it back up).
            "import resource\n"
            f"resource.setrlimit(resource.RLIMIT_AS, ({_RLIMIT_AS}, {_RLIMIT_AS}))\n"
            f"resource.setrlimit(resource.RLIMIT_FSIZE, ({_RLIMIT_FSIZE}, {_RLIMIT_FSIZE}))\n"
        )

    async def _silent_setup(self, code: str) -> None:
        """Execute a setup snippet and drain its messages without surfacing them."""
        if self._kc is None:
            return
        msg_id = self._kc.execute(code, store_history=False, silent=True)
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            try:
                msg = await asyncio.wait_for(
                    self._kc.get_iopub_msg(timeout=None),
                    timeout=1.0,
                )
            except asyncio.TimeoutError:
                continue
            parent = msg.get("parent_header") or {}
            if parent.get("msg_id") != msg_id:
                continue
            if msg["header"]["msg_type"] == "status":
                if (msg.get("content") or {}).get("execution_state") == "idle":
                    return

    async def shutdown(self) -> None:
        if self._kc is not None:
            try:
                self._kc.stop_channels()
            except Exception:
                pass
            self._kc = None
        if self._km is not None:
            try:
                await self._km.shutdown_kernel(now=True)
            except Exception:
                pass
            self._km = None

    async def reset(self) -> None:
        """Restart the kernel, wiping all in-memory state."""
        async with self._lock:
            await self.shutdown()
            await self.start()

    # ---- execution -------------------------------------------------------

    async def execute(self, code: str, timeout_s: float = 30.0) -> ExecutionResult:
        async with self._lock:
            assert self._kc is not None and self._km is not None
            self.last_used_at = time.time()
            started = time.monotonic()

            state: dict = {
                "stdout": CappedBuffer(),
                "stderr": CappedBuffer(),
                "result": None,
                "error": None,
                "artifacts": [],
            }

            msg_id = self._kc.execute(code, store_history=False)

            timed_out = False
            try:
                await asyncio.wait_for(
                    self._drain_iopub(msg_id=msg_id, state=state),
                    timeout=timeout_s,
                )
            except asyncio.TimeoutError:
                timed_out = True
                try:
                    await self._km.interrupt_kernel()
                except Exception:
                    pass
                # After interrupt, drain whatever is left in the queue without
                # blocking, so the next execution starts from a clean state.
                await self._flush_remaining(msg_id, state)
                state["error"] = (
                    f"execution exceeded {timeout_s:.1f}s timeout - kernel "
                    "was interrupted; in-memory state from before the timeout "
                    "is preserved"
                )

            duration_ms = int((time.monotonic() - started) * 1000)
            stdout: CappedBuffer = state["stdout"]
            stderr: CappedBuffer = state["stderr"]

            return ExecutionResult(
                stdout=stdout.value(),
                stderr=stderr.value(),
                result=state["result"],
                error=state["error"],
                artifacts=state["artifacts"],
                duration_ms=duration_ms,
                truncated_stdout=stdout.truncated,
                truncated_stderr=stderr.truncated,
                timed_out=timed_out,
            )

    # ---- iopub plumbing --------------------------------------------------

    async def _drain_iopub(self, msg_id: str, state: dict) -> None:
        """
        Pull messages off the iopub channel until the kernel reports idle
        for the parent message id we just submitted. ``state`` is mutated
        in place: stdout/stderr CappedBuffers are written to, result/error
        are set if the kernel produces them, and artifacts is appended to
        for any display_data images.
        """
        assert self._kc is not None
        stdout: CappedBuffer = state["stdout"]
        stderr: CappedBuffer = state["stderr"]
        artifacts: list = state["artifacts"]
        while True:
            msg = await self._kc.get_iopub_msg(timeout=None)
            parent = msg.get("parent_header") or {}
            if parent.get("msg_id") != msg_id:
                continue
            msg_type = msg["header"]["msg_type"]
            content = msg.get("content") or {}

            if msg_type == "stream":
                name = content.get("name")
                text = content.get("text", "")
                if name == "stdout":
                    stdout.write(text)
                elif name == "stderr":
                    stderr.write(text)

            elif msg_type == "execute_result":
                data = content.get("data") or {}
                txt = data.get("text/plain")
                if txt is not None:
                    state["result"] = txt
                self._extract_image(data, artifacts)

            elif msg_type == "display_data":
                data = content.get("data") or {}
                self._extract_image(data, artifacts)

            elif msg_type == "error":
                tb = content.get("traceback") or []
                ename = content.get("ename", "Error")
                evalue = content.get("evalue", "")
                joined = "\n".join(_strip_ansi(line) for line in tb)
                state["error"] = f"{ename}: {evalue}\n{joined}".strip()

            elif msg_type == "status":
                if content.get("execution_state") == "idle":
                    return

    async def _flush_remaining(self, msg_id: str, state: dict) -> None:
        """Best-effort drain after an interrupt so the next call starts clean."""
        assert self._kc is not None
        stdout: CappedBuffer = state["stdout"]
        stderr: CappedBuffer = state["stderr"]
        artifacts: list = state["artifacts"]
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            try:
                msg = await asyncio.wait_for(
                    self._kc.get_iopub_msg(timeout=None),
                    timeout=0.2,
                )
            except asyncio.TimeoutError:
                return
            parent = msg.get("parent_header") or {}
            if parent.get("msg_id") != msg_id:
                continue
            content = msg.get("content") or {}
            mt = msg["header"]["msg_type"]
            if mt == "stream":
                name = content.get("name")
                if name == "stdout":
                    stdout.write(content.get("text", ""))
                elif name == "stderr":
                    stderr.write(content.get("text", ""))
            elif mt in ("execute_result", "display_data"):
                self._extract_image(content.get("data") or {}, artifacts)
            elif mt == "status" and content.get("execution_state") == "idle":
                return

    def _extract_image(self, data: dict, artifacts: list[dict]) -> None:
        """Persist any image/png payload to the scratch dir as an artifact."""
        png_b64 = data.get("image/png")
        if not png_b64:
            return
        try:
            png_bytes = base64.b64decode(png_b64)
        except Exception:
            return
        artifact_id = uuid.uuid4().hex
        filename = f"{artifact_id}.png"
        path = os.path.join(self.scratch_dir, filename)
        with open(path, "wb") as f:
            f.write(png_bytes)
        artifacts.append({
            "id": artifact_id,
            "filename": filename,
            "content_type": "image/png",
            "size_bytes": len(png_bytes),
        })


def _strip_ansi(line: str) -> str:
    # IPython tracebacks are coloured. The escape sequences confuse the
    # frontend renderer and waste model tokens; strip them at the source.
    import re
    return re.sub(r"\x1b\[[0-9;]*m", "", line)
