"""
KernelHandle - one Jupyter kernel, one conversation.

Wraps jupyter_client.AsyncKernelManager so that:

- The kernel is launched through ``isolation.jail_prefix``: its own network
  namespace (loopback only), a uid leased to this conversation alone, no
  capabilities, no_new_privs and hard rlimits. jupyter_client reaches it over
  Unix sockets (``transport="ipc"``) in a runtime dir only that uid and the
  service can enter, since TCP cannot cross the namespace. firejail was used
  before 2026-09-30 and silently did nothing inside the container.
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
import json
import mimetypes
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from jupyter_client.manager import AsyncKernelManager

from . import isolation
from .output_cap import CappedBuffer


# The manifest lives in isolation.META_ROOT, out of the kernel's reach: in the
# scratch dir the kernel could rewrite it and point entries at other files.
_ARTIFACT_MANIFEST_FILENAME = "_artifacts.json"


# Stock mimetypes.guess_type misses a lot of formats common in scientific
# Python (xlsx/docx/parquet/h5/...). We patch the global mimetypes table at
# import time so artifacts get a useful Content-Type instead of falling back
# to application/octet-stream and rendering as a generic download in the
# frontend. add_type is idempotent and keyed on extension, so re-imports
# won't drift.
def _register_extra_mimetypes() -> None:
    extras = {
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xls": "application/vnd.ms-excel",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc": "application/msword",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".csv": "text/csv",
        ".tsv": "text/tab-separated-values",
        ".md": "text/markdown",
        ".parquet": "application/vnd.apache.parquet",
        ".feather": "application/vnd.apache.arrow.file",
        ".arrow": "application/vnd.apache.arrow.file",
        ".h5": "application/x-hdf5",
        ".hdf5": "application/x-hdf5",
        ".nc": "application/x-netcdf",
        ".npy": "application/x-numpy",
        ".npz": "application/x-numpy",
        ".pkl": "application/x-pickle",
        ".pickle": "application/x-pickle",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
        ".webp": "image/webp",
    }
    import mimetypes as _m
    for ext, ctype in extras.items():
        _m.add_type(ctype, ext)


_register_extra_mimetypes()


# ----------------------------------------------------------------------------
# Isolated launch
# ----------------------------------------------------------------------------

class IsolatedKernelManager(AsyncKernelManager):
    """AsyncKernelManager that launches the kernel through the isolation
    wrapper as `jail_uid`, and hands that uid the connection file (written
    0600 as root by jupyter_client, which the kernel could not read)."""

    jail_uid: int = 0

    def format_kernel_cmd(self, extra_arguments=None):  # type: ignore[override]
        cmd = super().format_kernel_cmd(extra_arguments=extra_arguments)
        return isolation.jail_prefix(self.jail_uid, isolation.KERNEL_LIMITS) + cmd

    def write_connection_file(self, **kwargs):  # type: ignore[override]
        result = super().write_connection_file(**kwargs)
        os.chown(self.connection_file, self.jail_uid, self.jail_uid)
        return result


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
        self._km: Optional[IsolatedKernelManager] = None
        self._uid: Optional[int] = None
        self._kc = None  # AsyncKernelClient
        self._lock = asyncio.Lock()
        self.created_at: float = time.time()
        self.last_used_at: float = time.time()
        # Per-kernel artifact bookkeeping. The manifest is the cumulative list
        # of artifacts surfaced from this conversation; the served filenames
        # set is a fast lookup so the post-execute scan doesn't re-emit files
        # that were already turned into artifacts on a previous turn.
        self._manifest_path = os.path.join(
            isolation.meta_dir(conversation_id), _ARTIFACT_MANIFEST_FILENAME
        )
        self._manifest: list[dict] = self._load_manifest()
        self._known_files: set[str] = {
            entry["filename"] for entry in self._manifest
        }

    # ---- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        if self._km is not None:
            return
        uid = await isolation.pool.acquire(self.conversation_id)
        try:
            home = isolation.fresh_runtime_dir(uid)
            km = IsolatedKernelManager(transport="ipc", ip=os.path.join(home, "kernel"))
            km.jail_uid = uid
            km.connection_file = os.path.join(home, "kernel.json")
            # Cwd inside the kernel: the conversation's scratch directory. Any
            # `open('foo.csv')` ends up under /scratch/{cid}/, the only place
            # this uid can write besides its own home.
            await km.start_kernel(cwd=self.scratch_dir, env=isolation.user_env(home))
            kc = km.client()
            kc.start_channels()
            try:
                await kc.wait_for_ready(timeout=30)
            except RuntimeError:
                await km.shutdown_kernel(now=True)
                raise
        except BaseException:
            await isolation.pool.release(self.conversation_id)
            raise
        self._uid = uid
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
        if self._uid is not None:
            self._uid = None
            await isolation.pool.release(self.conversation_id)

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

            # Surface any files the user code wrote to the scratch dir
            # this turn as artifacts, alongside the inline images that the
            # iopub handler already captured. Done after the timeout branch
            # so partial outputs from interrupted runs still get exposed.
            self._scan_for_new_files(state["artifacts"])

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
        try:
            isolation.write_new_file(os.path.join(self.scratch_dir, filename), png_bytes)
        except OSError:
            return
        entry = {
            "id": artifact_id,
            "filename": filename,
            "content_type": "image/png",
            "size_bytes": len(png_bytes),
        }
        artifacts.append(entry)
        self._register_artifact(entry)

    # ---- artifact manifest -------------------------------------------------

    def _load_manifest(self) -> list[dict]:
        """Load the cumulative artifact manifest from disk if present."""
        try:
            with open(self._manifest_path) as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return []

    def _save_manifest(self) -> None:
        try:
            tmp = self._manifest_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self._manifest, f)
            os.replace(tmp, self._manifest_path)
        except OSError:
            pass

    def _register_artifact(self, entry: dict) -> None:
        self._manifest.append(entry)
        self._known_files.add(entry["filename"])
        self._save_manifest()

    def _scan_for_new_files(self, artifacts: list[dict]) -> None:
        """
        Walk the scratch dir for files the user code wrote during this
        execution and surface each one as a fresh artifact. Files already
        in the manifest (from earlier turns or from the inline-image path)
        are skipped. Internal bookkeeping files (the manifest itself) are
        ignored. New files get a uuid id, an extension-sniffed content
        type, and a size_bytes from the filesystem.
        """
        try:
            entries = os.listdir(self.scratch_dir)
        except OSError:
            return
        for fname in sorted(entries):
            if fname in self._known_files:
                continue
            full = os.path.join(self.scratch_dir, fname)
            # lstat: a symlink the kernel planted is never surfaced
            if not isolation.is_regular_nofollow(full):
                continue
            try:
                size = os.lstat(full).st_size
            except OSError:
                continue
            ctype, _ = mimetypes.guess_type(fname)
            ctype = ctype or "application/octet-stream"
            entry = {
                "id": uuid.uuid4().hex,
                "filename": fname,
                "content_type": ctype,
                "size_bytes": size,
            }
            artifacts.append(entry)
            self._register_artifact(entry)


def _strip_ansi(line: str) -> str:
    # IPython tracebacks are coloured. The escape sequences confuse the
    # frontend renderer and waste model tokens; strip them at the source.
    import re
    return re.sub(r"\x1b\[[0-9;]*m", "", line)
