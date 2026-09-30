"""
sandbox-svc: tiny FastAPI service that wraps Jupyter kernels.

Lives in its own container on a private docker network reachable only
from the retrieval container. There is no auth on the HTTP surface; the
docker network is the trust boundary, which is why user code must never
share the service's network namespace (see isolation.py).

Endpoints:

  POST   /exec/{conversation_id}     - run code in this conversation's kernel
  POST   /reset/{conversation_id}    - restart the kernel (wipe in-memory state)
  DELETE /kernels/{conversation_id}  - shut the kernel down (called by the
                                       retrieval container when a chat is
                                       deleted, or by the idle reaper)
  GET    /artifacts/{cid}/{aid}      - return a file the kernel produced
  GET    /healthz                    - liveness check

Kernels are spawned lazily on first /exec for a given conversation. Each
KernelHandle owns a per-conversation scratch directory under /scratch.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Path
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import isolation
from .kernel import KernelHandle
from . import latex as latex_compiler


# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------

SCRATCH_ROOT = os.environ.get("SANDBOX_SCRATCH_DIR", "/scratch")
IDLE_TTL_S = float(os.environ.get("SANDBOX_IDLE_TTL_S", "900"))  # 15 min
REAPER_INTERVAL_S = float(os.environ.get("SANDBOX_REAPER_INTERVAL_S", "60"))
MAX_TIMEOUT_S = float(os.environ.get("SANDBOX_MAX_TIMEOUT_S", "120"))
# Local development only: run code even when the isolation self-test fails.
ALLOW_UNISOLATED = os.environ.get("SANDBOX_ALLOW_UNISOLATED") == "1"

# Result of isolation.self_test() at startup; exec and latex refuse to run
# user code unless it is ok.
ISOLATION: dict = {"ok": False, "error": "self-test has not run"}


def _require_runnable(conversation_id: str) -> None:
    if not isolation.valid_conversation_id(conversation_id):
        raise HTTPException(status_code=400, detail="bad conversation id")
    if not ISOLATION.get("ok") and not ALLOW_UNISOLATED:
        raise HTTPException(status_code=503,
                            detail="sandbox isolation self-test failed; refusing to run code")

logger = logging.getLogger("sandbox-svc")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# ----------------------------------------------------------------------------
# Kernel registry + reaper
# ----------------------------------------------------------------------------

class KernelRegistry:
    def __init__(self) -> None:
        self._kernels: dict[str, KernelHandle] = {}
        self._lock = asyncio.Lock()

    async def get_or_start(self, conversation_id: str) -> KernelHandle:
        async with self._lock:
            handle = self._kernels.get(conversation_id)
            if handle is None:
                handle = KernelHandle(conversation_id, SCRATCH_ROOT)
                self._kernels[conversation_id] = handle
        if handle._km is None:  # type: ignore[attr-defined]
            try:
                await handle.start()
            except isolation.SandboxBusy as exc:
                async with self._lock:
                    self._kernels.pop(conversation_id, None)
                raise HTTPException(status_code=503, detail=str(exc))
        return handle

    async def get(self, conversation_id: str) -> Optional[KernelHandle]:
        return self._kernels.get(conversation_id)

    async def shutdown(self, conversation_id: str) -> bool:
        async with self._lock:
            handle = self._kernels.pop(conversation_id, None)
        if handle is None:
            return False
        await handle.shutdown()
        return True

    async def shutdown_all(self) -> None:
        async with self._lock:
            handles = list(self._kernels.values())
            self._kernels.clear()
        for h in handles:
            try:
                await h.shutdown()
            except Exception:
                logger.exception("error shutting down kernel %s", h.conversation_id)

    def snapshot(self) -> list[tuple[str, float]]:
        return [(cid, h.last_used_at) for cid, h in self._kernels.items()]


registry = KernelRegistry()


async def _reaper_loop() -> None:
    """
    Walk the registry every REAPER_INTERVAL_S and shut down any kernel
    that has been idle for more than IDLE_TTL_S. The conversation can keep
    being chatted in: the next /exec call will spawn a fresh kernel and
    only the in-memory state from earlier turns is lost (files in /scratch
    survive because the scratch dir is keyed by conversation_id).
    """
    while True:
        try:
            await asyncio.sleep(REAPER_INTERVAL_S)
            now = time.time()
            stale = [
                cid
                for cid, last_used in registry.snapshot()
                if now - last_used > IDLE_TTL_S
            ]
            for cid in stale:
                logger.info("reaping idle kernel %s", cid)
                await registry.shutdown(cid)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("reaper iteration failed")


# ----------------------------------------------------------------------------
# FastAPI app + lifespan
# ----------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    isolation.ensure_roots()
    ISOLATION.clear()
    ISOLATION.update(await isolation.self_test())
    (logger.info if ISOLATION.get("ok") else logger.error)(
        "sandbox-svc starting (isolation=%s, scratch=%s, idle_ttl=%.0fs)",
        ISOLATION, SCRATCH_ROOT, IDLE_TTL_S,
    )
    reaper_task = asyncio.create_task(_reaper_loop())
    try:
        yield
    finally:
        reaper_task.cancel()
        try:
            await reaper_task
        except (asyncio.CancelledError, Exception):
            pass
        await registry.shutdown_all()


app = FastAPI(title="munin-sandbox", lifespan=lifespan)


# ----------------------------------------------------------------------------
# Schemas
# ----------------------------------------------------------------------------

class ExecRequest(BaseModel):
    code: str = Field(..., description="Python source to execute")
    timeout_s: float = Field(30.0, ge=1, le=MAX_TIMEOUT_S)


class ExecResponse(BaseModel):
    stdout: str
    stderr: str
    result: Optional[str]
    error: Optional[str]
    artifacts: list[dict]
    duration_ms: int
    truncated_stdout: bool
    truncated_stderr: bool
    timed_out: bool
    isolated: bool


class LatexRequest(BaseModel):
    source: str = Field(..., description="Main .tex file contents")
    bibliography: Optional[str] = Field(
        None, description="Optional .bib file contents"
    )
    extra_files: Optional[dict[str, str]] = Field(
        None,
        description=(
            "Optional filename → content map. Values are raw text by "
            "default; values prefixed with 'base64:' are decoded as "
            "binary (for images, .cls/.bst/.sty files, etc.)."
        ),
    )
    timeout_s: float = Field(60.0, ge=1, le=MAX_TIMEOUT_S)


class LatexArtifactDict(BaseModel):
    id: str
    filename: str
    content_type: str
    size_bytes: int


class LatexResponse(BaseModel):
    success: bool
    tex_artifact: Optional[LatexArtifactDict]
    pdf_artifact: Optional[LatexArtifactDict]
    errors: list[dict]
    warnings: list[str]
    log_tail: str
    stdout_tail: str
    duration_ms: int
    timed_out: bool
    error_message: Optional[str]
    isolated: bool


# ----------------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------------

@app.get("/healthz")
async def healthz():
    body = {
        "status": "ok" if ISOLATION.get("ok") else "unisolated",
        "kernels": len(registry.snapshot()),
        "isolation": ISOLATION,
        "scratch": SCRATCH_ROOT,
        "idle_ttl_s": IDLE_TTL_S,
    }
    # Unhealthy without isolation, so docker and deploy.sh surface it.
    return JSONResponse(body, status_code=200 if ISOLATION.get("ok") else 503)


@app.post("/exec/{conversation_id}", response_model=ExecResponse)
async def exec_code(
    body: ExecRequest,
    conversation_id: str = Path(..., min_length=1),
) -> ExecResponse:
    _require_runnable(conversation_id)
    handle = await registry.get_or_start(conversation_id)
    result = await handle.execute(body.code, timeout_s=body.timeout_s)
    return ExecResponse(
        stdout=result.stdout,
        stderr=result.stderr,
        result=result.result,
        error=result.error,
        artifacts=result.artifacts,
        duration_ms=result.duration_ms,
        truncated_stdout=result.truncated_stdout,
        truncated_stderr=result.truncated_stderr,
        timed_out=result.timed_out,
        isolated=bool(ISOLATION.get("ok")),
    )


@app.post("/latex/{conversation_id}", response_model=LatexResponse)
async def compile_latex_route(
    body: LatexRequest,
    conversation_id: str = Path(..., min_length=1),
) -> LatexResponse:
    """
    §18: compile a LaTeX document and return structured error/artifact info.

    Independent of the kernel registry: we don't spin up a Jupyter
    kernel for a compile. We still share the conversation's scratch
    directory so the compiled .tex and .pdf live alongside any other
    files the kernel has produced in the same conversation.
    """
    _require_runnable(conversation_id)
    result = await latex_compiler.compile_latex(
        scratch_root=SCRATCH_ROOT,
        conversation_id=conversation_id,
        source=body.source,
        bibliography=body.bibliography,
        extra_files=body.extra_files,
        timeout_s=body.timeout_s,
    )
    return LatexResponse(
        success=result.success,
        tex_artifact=(
            LatexArtifactDict(**result.tex_artifact.__dict__)
            if result.tex_artifact else None
        ),
        pdf_artifact=(
            LatexArtifactDict(**result.pdf_artifact.__dict__)
            if result.pdf_artifact else None
        ),
        errors=result.errors,
        warnings=result.warnings,
        log_tail=result.log_tail,
        stdout_tail=result.stdout_tail,
        duration_ms=result.duration_ms,
        timed_out=result.timed_out,
        error_message=result.error_message,
        isolated=bool(ISOLATION.get("ok")),
    )


@app.post("/reset/{conversation_id}")
async def reset_kernel(conversation_id: str) -> dict:
    _require_runnable(conversation_id)
    handle = await registry.get(conversation_id)
    if handle is None:
        return {"reset": False, "reason": "no kernel running"}
    await handle.reset()
    return {"reset": True}


@app.delete("/kernels/{conversation_id}")
async def shutdown_kernel(conversation_id: str) -> dict:
    removed = await registry.shutdown(conversation_id)
    return {"shutdown": removed}


@app.get("/artifacts/{conversation_id}/{artifact_id}")
async def get_artifact(conversation_id: str, artifact_id: str):
    # Defence-in-depth: refuse anything that could escape the scratch
    # directory. The registry never produces paths outside /scratch but
    # the URL is user-influenced, so we validate the shape here.
    if not artifact_id.replace("-", "").isalnum() or "/" in artifact_id:
        raise HTTPException(status_code=400, detail="bad artifact id")
    if not isolation.valid_conversation_id(conversation_id):
        raise HTTPException(status_code=400, detail="bad conversation id")

    conv_dir = os.path.join(SCRATCH_ROOT, conversation_id)
    if not os.path.isdir(conv_dir):
        raise HTTPException(status_code=404, detail="artifact not found")
    manifest_dir = os.path.join(isolation.META_ROOT, conversation_id)

    # Look up the artifact through the manifest written by KernelHandle.
    # The manifest preserves the original filename and the sniffed content
    # type, so xlsx/csv/png all serve correctly without filename guessing.
    #
    # §18 added a sibling ``_latex_artifacts.json`` for compile_latex
    # outputs (kept separate from KernelHandle's manifest to avoid a
    # write-race). Check both manifests here so .tex and .pdf artifacts
    # served via the same URL shape.
    import json as _json
    manifest: list[dict] = []
    for mfname in ("_artifacts.json", "_latex_artifacts.json"):
        mpath = os.path.join(manifest_dir, mfname)
        try:
            with open(mpath) as f:
                data = _json.load(f)
            if isinstance(data, list):
                manifest.extend(e for e in data if isinstance(e, dict))
        except (FileNotFoundError, ValueError, OSError):
            continue
    if not manifest:
        raise HTTPException(status_code=404, detail="artifact not found")
    entry = next(
        (e for e in manifest if e.get("id") == artifact_id),
        None,
    )
    if entry is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    fname = entry.get("filename") or ""
    if not fname or "/" in fname or fname.startswith(".."):
        raise HTTPException(status_code=400, detail="bad manifest entry")
    # The conversation's uid owns the dir and could have replaced the file
    # with a link to anything this root process can read; only a regular
    # file, opened without following links, is served.
    fd = isolation.open_regular_nofollow(conv_dir, fname)
    if fd is None:
        raise HTTPException(status_code=404, detail="artifact file missing")
    size = os.fstat(fd).st_size
    fh = os.fdopen(fd, "rb")

    def _chunks():
        with fh:
            while chunk := fh.read(1 << 16):
                yield chunk

    # Same Content-Disposition as the FileResponse this replaced
    from urllib.parse import quote
    quoted = quote(fname)
    disposition = (f'attachment; filename="{fname}"' if quoted == fname
                   else f"attachment; filename*=utf-8''{quoted}")
    return StreamingResponse(
        _chunks(),
        media_type=entry.get("content_type") or "application/octet-stream",
        headers={"Content-Length": str(size), "Content-Disposition": disposition},
    )
