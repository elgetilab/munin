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
                                       (immutable: served from the blob store)
  GET    /artifacts/{cid}            - list the conversation's artifacts
  GET    /bundle/{cid}?ids=a,b       - zip of artifacts, inputs, code, env
  POST   /inputs/{cid}?name=f.csv    - store an uploaded input file (raw body;
                                       a .zip is extracted); mirrored read-only
                                       into /scratch/{cid}/inputs/
  GET    /inputs/{cid}               - list the conversation's input files
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

from fastapi import FastAPI, HTTPException, Path, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import isolation
from .kernel import KernelHandle
from . import latex as latex_compiler
from . import bundle as bundle_mod
from .store import ConversationStore, InputError, INPUT_MAX_BYTES, LATEX_MANIFEST_FILENAME


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
    warnings: list[str] = []
    exec_seq: Optional[int] = None
    code_sha: Optional[str] = None


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
        warnings=result.warnings,
        exec_seq=result.exec_seq,
        code_sha=result.code_sha,
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


def _require_cid(conversation_id: str) -> None:
    if not isolation.valid_conversation_id(conversation_id):
        raise HTTPException(status_code=400, detail="bad conversation id")


async def _store_for(conversation_id: str) -> ConversationStore:
    """The live kernel's store when there is one, else a fresh view of the
    same on-disk state (manifests are rewritten after every change)."""
    handle = await registry.get(conversation_id)
    if handle is not None:
        return handle.store
    return ConversationStore(conversation_id, os.path.join(SCRATCH_ROOT, conversation_id))


def _latex_entries(conversation_id: str) -> list[dict]:
    import json as _json
    path = os.path.join(isolation.META_ROOT, conversation_id, LATEX_MANIFEST_FILENAME)
    try:
        with open(path) as f:
            data = _json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return []
    return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []


def _public(entry: dict) -> dict:
    """Artifact metadata for listings (the figure description is large and
    has its own place in bundles)."""
    return {k: v for k, v in entry.items() if k not in ("figure", "mtime_ns")}


def _disposition(fname: str) -> str:
    from urllib.parse import quote
    base = os.path.basename(fname) or "artifact"
    quoted = quote(base)
    return (f'attachment; filename="{base}"' if quoted == base
            else f"attachment; filename*=utf-8''{quoted}")


def _stream_fd(fd: int):
    fh = os.fdopen(fd, "rb")

    def _chunks():
        with fh:
            while chunk := fh.read(1 << 16):
                yield chunk
    return _chunks()


@app.get("/artifacts/{conversation_id}")
async def list_artifacts(conversation_id: str, include_figures: bool = False) -> dict:
    _require_cid(conversation_id)
    store = await _store_for(conversation_id)
    items = [e if include_figures else _public(e) for e in store.manifest]
    items += [dict(e, kind="latex") for e in _latex_entries(conversation_id)]
    return {"artifacts": items}


@app.get("/artifacts/{conversation_id}/{artifact_id}")
async def get_artifact(conversation_id: str, artifact_id: str):
    # Defence-in-depth: refuse anything that could escape the scratch
    # directory. The URL is user-influenced, so validate its shape.
    if not artifact_id.replace("-", "").isalnum() or "/" in artifact_id:
        raise HTTPException(status_code=400, detail="bad artifact id")
    _require_cid(conversation_id)
    conv_dir = os.path.join(SCRATCH_ROOT, conversation_id)
    if not os.path.isdir(os.path.join(isolation.META_ROOT, conversation_id)):
        raise HTTPException(status_code=404, detail="artifact not found")
    store = await _store_for(conversation_id)
    entry = store.find(artifact_id)
    if entry is None:
        entry = next((e for e in _latex_entries(conversation_id) if e.get("id") == artifact_id),
                     None)
    if entry is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    fname = entry.get("filename") or ""
    if entry.get("sha256"):
        # Immutable copy: exactly the bytes the artifact had when created.
        fd = store.open_blob(entry["sha256"])
    else:
        # Entries from before the blob store: the live file at the top level
        # of the scratch dir. The conversation's uid owns that dir and could
        # have replaced the file with a link to anything this root process
        # can read; only a regular file, opened without following links.
        if not fname or "/" in fname or fname.startswith(".."):
            raise HTTPException(status_code=400, detail="bad manifest entry")
        fd = isolation.open_regular_nofollow(conv_dir, fname)
    if fd is None:
        raise HTTPException(status_code=404, detail="artifact file missing")
    size = os.fstat(fd).st_size
    headers = {"Content-Length": str(size), "Content-Disposition": _disposition(fname)}
    if entry.get("sha256"):
        headers["X-Munin-Sha256"] = entry["sha256"]
        headers["ETag"] = f'"{entry["sha256"]}"'
    return StreamingResponse(
        _stream_fd(fd),
        media_type=entry.get("content_type") or "application/octet-stream",
        headers=headers,
    )


@app.get("/bundle/{conversation_id}")
async def get_bundle(conversation_id: str, ids: Optional[str] = Query(None)):
    _require_cid(conversation_id)
    if not os.path.isdir(os.path.join(isolation.META_ROOT, conversation_id)):
        raise HTTPException(status_code=404, detail="nothing to bundle")
    store = await _store_for(conversation_id)
    wanted = [i for i in (ids or "").split(",") if i] or None
    if wanted and any(not i.isalnum() for i in wanted):
        raise HTTPException(status_code=400, detail="bad artifact id")
    try:
        path = await run_in_threadpool(bundle_mod.build_bundle, store, wanted,
                                       _latex_entries(conversation_id))
    except bundle_mod.BundleError as exc:
        raise HTTPException(status_code=404 if "unknown" in str(exc) or "nothing" in str(exc)
                            else 413, detail=str(exc))
    size = os.path.getsize(path)
    fh = open(path, "rb")
    os.unlink(path)  # the open handle keeps the data until streaming ends

    def _chunks():
        with fh:
            while chunk := fh.read(1 << 20):
                yield chunk

    stamp = time.strftime("%Y%m%d-%H%M%S")
    return StreamingResponse(
        _chunks(), media_type="application/zip",
        headers={"Content-Length": str(size),
                 "Content-Disposition": f'attachment; filename="munin-analysis-{stamp}.zip"'},
    )


@app.post("/inputs/{conversation_id}")
async def put_input(conversation_id: str, request: Request,
                    name: str = Query(..., min_length=1, max_length=255),
                    extract: bool = Query(True)) -> dict:
    _require_cid(conversation_id)
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > INPUT_MAX_BYTES:
        raise HTTPException(status_code=413,
                            detail=f"file is larger than the {INPUT_MAX_BYTES // 1024 ** 2} MB limit")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > INPUT_MAX_BYTES:
            raise HTTPException(status_code=413,
                                detail=f"file is larger than the {INPUT_MAX_BYTES // 1024 ** 2} MB limit")
        chunks.append(chunk)
    store = await _store_for(conversation_id)
    os.makedirs(os.path.join(SCRATCH_ROOT, conversation_id), exist_ok=True)
    try:
        added = await run_in_threadpool(store.add_input, name, b"".join(chunks),
                                        extract_zip=extract)
    except InputError as exc:
        raise HTTPException(status_code=413 if "larger" in str(exc) or "full" in str(exc)
                            or "exceed" in str(exc) else 400, detail=str(exc))
    await run_in_threadpool(store.sync_inputs)
    return {"added": added, "dir": "inputs"}


@app.get("/inputs/{conversation_id}")
async def list_inputs(conversation_id: str) -> dict:
    _require_cid(conversation_id)
    store = await _store_for(conversation_id)
    store._reload_inputs()
    return {"inputs": store.inputs, "dir": "inputs"}
