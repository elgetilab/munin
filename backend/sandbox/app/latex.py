"""
LaTeX compiler for the sandbox sidecar (§18).

Runs pdflatex through the sandbox's isolation wrapper (own network namespace,
the conversation's leased uid, hard rlimits) over user-provided .tex source and returns a
structured result: a PDF artifact on success, structured error info on
failure, and the .tex source always, so users can grab it even if the
compile broke.

Design:

- Each compile gets an ephemeral working dir at
  ``/scratch/{cid}/latex-{uuid}/`` so aux files (.aux, .log, .toc, .bbl,
  .out, .fls) don't litter the conversation's scratch root. After
  compile the subdir is wiped; the final .tex and .pdf are copied up
  to the conv-root under unique names (``latex_{uuid}.tex``,
  ``latex_{uuid}.pdf``) so the existing ``/artifacts/{cid}/{aid}``
  route serves them without modification.
- Artifacts are registered in a sibling manifest ``_latex_artifacts.json``
  rather than the kernel's ``_artifacts.json``, to avoid racing with
  KernelHandle's manifest writer. Both live in isolation.META_ROOT, out of
  user code's reach. The GET ``/artifacts`` route checks both manifests.
- The work subdir belongs to the conversation's uid while pdflatex runs, and
  a kernel of the same conversation could swap its files for symlinks, so
  every read of it as root goes through isolation's no-follow helpers.
- pdflatex invocation: ``-no-shell-escape -interaction=nonstopmode
  -halt-on-error -file-line-error``. The first two block ``\\write18``
  shell-escapes and stop pdflatex from prompting for user input on
  errors. ``-halt-on-error`` returns non-zero on the first hard error
  so we don't waste time on a cascade. ``-file-line-error`` gives us
  parseable ``file:line: message`` output.
- BibTeX cycle: when ``bibliography`` is provided, we do the textbook
  ``pdflatex → bibtex → pdflatex → pdflatex`` sequence so citations
  resolve and the page numbers settle.
- Two-pass when warranted: if the log from the first pdflatex pass
  contains ``Rerun to get cross-references right`` or references like
  ``Label(s) may have changed``, we run a second pass even without a
  .bib file.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from . import isolation


_LATEX_MANIFEST_FILENAME = "_latex_artifacts.json"
_MAX_PDF_SIZE = 25 * 1024 * 1024  # 25 MB
_LOG_TAIL_LINES = 50
_MAX_STRUCTURED_ERRORS = 10
_MAX_EXTRA_FILES = 20
_MAX_EXTRA_FILE_SIZE = 10 * 1024 * 1024  # 10 MB per file


@dataclass
class LatexArtifact:
    id: str
    filename: str
    content_type: str
    size_bytes: int


@dataclass
class LatexResult:
    success: bool
    tex_artifact: Optional[LatexArtifact]
    pdf_artifact: Optional[LatexArtifact]
    errors: list[dict]
    warnings: list[str]
    log_tail: str
    stdout_tail: str
    duration_ms: int
    timed_out: bool
    error_message: Optional[str] = None


_PDFLATEX_FLAGS = [
    "-no-shell-escape",
    "-interaction=nonstopmode",
    "-halt-on-error",
    "-file-line-error",
]


# Parse "file:line: message" lines pdflatex emits with -file-line-error.
_ERROR_LINE_RE = re.compile(r"^([^:\s]+):(\d+):\s*(.*)$")
# Fallback: classic pdflatex errors begin with "! "
_BANG_ERROR_RE = re.compile(r"^!\s*(.*)$")
# "Rerun" signals
_RERUN_RE = re.compile(
    r"Rerun to get|Label\(s\) may have changed|Rerun LaTeX",
    re.IGNORECASE,
)


async def _run_subprocess(
    argv: list[str],
    cwd: str,
    timeout_s: float,
) -> tuple[int, str, str, bool]:
    """Run a subprocess and return (rc, stdout, stderr, timed_out)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=isolation.user_env(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as e:
        return 127, "", f"binary not found: {e}", False

    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_s
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await proc.wait()
        except Exception:
            pass
        return 124, "", "pdflatex timed out", True

    return (
        proc.returncode or 0,
        stdout_b.decode("utf-8", errors="replace"),
        stderr_b.decode("utf-8", errors="replace"),
        False,
    )


def _parse_log(log_text: str) -> tuple[list[dict], list[str], str]:
    """
    Scrape a pdflatex log for structured errors and warnings.

    Returns (errors, warnings, log_tail). Errors are capped at
    _MAX_STRUCTURED_ERRORS to keep the response tight.
    """
    errors: list[dict] = []
    warnings: list[str] = []
    lines = log_text.splitlines()

    for raw in lines:
        if len(errors) >= _MAX_STRUCTURED_ERRORS and len(warnings) >= 50:
            break
        line = raw.rstrip()
        if not line:
            continue
        m = _ERROR_LINE_RE.match(line)
        if m:
            fname, lineno, msg = m.group(1), int(m.group(2)), m.group(3)
            # file-line-error format fires for both errors and
            # warnings; heuristic: "Error" or "!" in message.
            if "Error" in msg or msg.startswith("!") or "Undefined" in msg:
                if len(errors) < _MAX_STRUCTURED_ERRORS:
                    errors.append({
                        "file": fname,
                        "line": lineno,
                        "message": msg.strip(),
                    })
            continue
        bm = _BANG_ERROR_RE.match(line)
        if bm and len(errors) < _MAX_STRUCTURED_ERRORS:
            errors.append({
                "file": None,
                "line": None,
                "message": bm.group(1).strip(),
            })
            continue
        if "Warning" in line and "LaTeX" in line:
            if len(warnings) < 50:
                warnings.append(line.strip())

    tail = "\n".join(lines[-_LOG_TAIL_LINES:])
    return errors, warnings, tail


def _read_work_file(work_dir: str, name: str) -> str:
    """Read a file pdflatex wrote, refusing a symlink in its place."""
    fd = isolation.open_regular_nofollow(work_dir, name)
    if fd is None:
        return ""
    with os.fdopen(fd, "rb") as f:
        return f.read().decode("utf-8", errors="replace")


def _load_latex_manifest(manifest_dir: str) -> list[dict]:
    path = os.path.join(manifest_dir, _LATEX_MANIFEST_FILENAME)
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def _save_latex_manifest(manifest_dir: str, manifest: list[dict]) -> None:
    path = os.path.join(manifest_dir, _LATEX_MANIFEST_FILENAME)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(manifest, f)
    os.replace(tmp, path)


def _register_latex_artifact(
    manifest_dir: str, entry: dict
) -> None:
    """
    Append one entry to the latex manifest. Callers should serialise
    this via the module-level lock below when multiple compiles could
    overlap for the same conversation.
    """
    manifest = _load_latex_manifest(manifest_dir)
    manifest.append(entry)
    _save_latex_manifest(manifest_dir, manifest)


# Per-conversation lock dict. Prevents two parallel compile_latex calls
# for the same conversation from racing on the manifest file. The lock
# cardinality is bounded by the kernel registry's idle reaper in main.py
# (conversations get cleaned up), so we don't need our own TTL here.
_CONV_LOCKS: dict[str, asyncio.Lock] = {}


def _get_conv_lock(conversation_id: str) -> asyncio.Lock:
    lock = _CONV_LOCKS.get(conversation_id)
    if lock is None:
        lock = asyncio.Lock()
        _CONV_LOCKS[conversation_id] = lock
    return lock


def _decode_extra_file(value: str) -> bytes:
    """
    ``extra_files`` values are strings. Plain text by default; values
    starting with ``base64:`` are decoded as binary. Used for shipping
    images, .cls/.bst/.sty files, etc. alongside the main .tex.
    """
    if value.startswith("base64:"):
        return base64.b64decode(value[len("base64:"):], validate=False)
    return value.encode("utf-8")


async def compile_latex(
    scratch_root: str,
    conversation_id: str,
    source: str,
    bibliography: Optional[str],
    extra_files: Optional[dict[str, str]],
    timeout_s: float,
) -> LatexResult:
    """
    Compile a LaTeX document and return a structured result.

    See module docstring for the full design. This function is
    exception-safe: it always returns a LatexResult, never raises to
    the caller. On unexpected errors (missing pdflatex binary, disk
    write failure), ``success=False`` and ``error_message`` is set.
    """
    started = time.monotonic()
    conv_dir = os.path.join(scratch_root, conversation_id)
    os.makedirs(conv_dir, exist_ok=True)
    meta = isolation.meta_dir(conversation_id)
    try:
        uid = await isolation.pool.acquire(conversation_id)
    except isolation.SandboxBusy as exc:
        return LatexResult(success=False, tex_artifact=None, pdf_artifact=None,
                           errors=[], warnings=[], log_tail="", stdout_tail="",
                           duration_ms=0, timed_out=False, error_message=str(exc))

    compile_id = uuid.uuid4().hex[:16]
    work_subdir = os.path.join(conv_dir, f"latex-{compile_id}")
    # Root-owned until the inputs are written, so user code of this
    # conversation cannot plant links under the names written below.
    try:
        os.mkdir(work_subdir, 0o700)
    except OSError:
        await isolation.pool.release(conversation_id)
        raise

    # Unique output filenames at the conversation root so they can be
    # served by the existing artifact route without subdir support.
    out_tex_name = f"latex_{compile_id}.tex"
    out_pdf_name = f"latex_{compile_id}.pdf"
    out_tex_path = os.path.join(conv_dir, out_tex_name)
    out_pdf_path = os.path.join(conv_dir, out_pdf_name)

    tex_artifact: Optional[LatexArtifact] = None
    pdf_artifact: Optional[LatexArtifact] = None
    errors: list[dict] = []
    warnings: list[str] = []
    log_tail = ""
    stdout_tail = ""
    timed_out = False
    error_message: Optional[str] = None

    async with _get_conv_lock(conversation_id):
        try:
            # --- Write inputs into the working subdir ---
            main_tex_path = os.path.join(work_subdir, "main.tex")
            isolation.write_new_file(main_tex_path, source.encode("utf-8"))
            if bibliography:
                isolation.write_new_file(os.path.join(work_subdir, "refs.bib"),
                                         bibliography.encode("utf-8"))
            if extra_files:
                if len(extra_files) > _MAX_EXTRA_FILES:
                    error_message = (
                        f"too many extra_files: {len(extra_files)} > {_MAX_EXTRA_FILES}"
                    )
                    raise RuntimeError(error_message)
                for fname, value in extra_files.items():
                    if "/" in fname or fname.startswith(".") or "\\" in fname:
                        error_message = f"invalid extra_files key: {fname!r}"
                        raise RuntimeError(error_message)
                    data = _decode_extra_file(value)
                    if len(data) > _MAX_EXTRA_FILE_SIZE:
                        error_message = (
                            f"extra_files[{fname!r}] too large: "
                            f"{len(data)} > {_MAX_EXTRA_FILE_SIZE}"
                        )
                        raise RuntimeError(error_message)
                    isolation.write_new_file(os.path.join(work_subdir, fname), data)

            # --- Always copy the .tex source out first, so the user
            # gets it even if the compile below fails. ---
            tex_size = isolation.copy_regular_nofollow(work_subdir, "main.tex", out_tex_path)
            if tex_size is None:
                error_message = "could not stage the .tex source"
                raise RuntimeError(error_message)
            tex_artifact = LatexArtifact(
                id=uuid.uuid4().hex,
                filename=out_tex_name,
                content_type="application/x-tex",
                size_bytes=tex_size,
            )
            _register_latex_artifact(meta, {
                "id": tex_artifact.id,
                "filename": tex_artifact.filename,
                "content_type": tex_artifact.content_type,
                "size_bytes": tex_artifact.size_bytes,
            })

            # --- hand the work dir to the conversation's uid, then pass 1 ---
            isolation.chown_tree(work_subdir, uid)
            prefix = isolation.jail_prefix(uid, isolation.LATEX_LIMITS)
            pdflatex_argv = prefix + [
                "pdflatex",
                *_PDFLATEX_FLAGS,
                "main.tex",
            ]

            async def _pdflatex_pass() -> tuple[int, str, str, bool]:
                return await _run_subprocess(
                    pdflatex_argv,
                    cwd=work_subdir,
                    timeout_s=timeout_s,
                )

            rc1, stdout1, _stderr1, to1 = await _pdflatex_pass()
            stdout_tail = stdout1[-4000:]
            if to1:
                timed_out = True
                error_message = "pdflatex pass 1 timed out"
                raise RuntimeError(error_message)

            log_text = _read_work_file(work_subdir, "main.log")

            if rc1 != 0:
                errors, warnings, log_tail = _parse_log(log_text)
                # No PDF; return with the .tex artifact only.
                raise RuntimeError("pdflatex pass 1 failed")

            needs_rerun = bool(_RERUN_RE.search(log_text))

            # --- BibTeX cycle if a .bib was given ---
            if bibliography:
                bibtex_argv = prefix + ["bibtex", "main"]
                rc_bib, _stdout_bib, _stderr_bib, to_bib = await _run_subprocess(
                    bibtex_argv,
                    cwd=work_subdir,
                    timeout_s=max(10.0, timeout_s / 3),
                )
                if to_bib:
                    timed_out = True
                    error_message = "bibtex timed out"
                    raise RuntimeError(error_message)
                # bibtex returns non-zero for warnings; we don't bail
                # here because the next pdflatex passes will surface
                # the actual citation failure if there is one.
                rc2, _stdout2, _stderr2, to2 = await _pdflatex_pass()
                if to2:
                    timed_out = True
                    error_message = "pdflatex pass 2 timed out"
                    raise RuntimeError(error_message)
                if rc2 != 0:
                    log_text = _read_work_file(work_subdir, "main.log")
                    errors, warnings, log_tail = _parse_log(log_text)
                    raise RuntimeError("pdflatex pass 2 failed")
                needs_rerun = True  # always do a final pass after bibtex

            if needs_rerun:
                rc3, _stdout3, _stderr3, to3 = await _pdflatex_pass()
                if to3:
                    timed_out = True
                    error_message = "pdflatex final pass timed out"
                    raise RuntimeError(error_message)
                if rc3 != 0:
                    log_text = _read_work_file(work_subdir, "main.log")
                    errors, warnings, log_tail = _parse_log(log_text)
                    raise RuntimeError("pdflatex final pass failed")

            # --- Parse warnings from the final log even on success ---
            log_text = _read_work_file(work_subdir, "main.log")
            _errors_final, warnings_final, log_tail = _parse_log(log_text)
            warnings = warnings_final

            # --- Copy the PDF out ---
            pdf_size = isolation.copy_regular_nofollow(work_subdir, "main.pdf", out_pdf_path)
            if pdf_size is None:
                error_message = "pdflatex reported success but main.pdf is missing"
                raise RuntimeError(error_message)
            if pdf_size > _MAX_PDF_SIZE:
                os.unlink(out_pdf_path)
                error_message = (
                    f"compiled PDF too large: {pdf_size} > {_MAX_PDF_SIZE}"
                )
                raise RuntimeError(error_message)
            pdf_artifact = LatexArtifact(
                id=uuid.uuid4().hex,
                filename=out_pdf_name,
                content_type="application/pdf",
                size_bytes=pdf_size,
            )
            _register_latex_artifact(meta, {
                "id": pdf_artifact.id,
                "filename": pdf_artifact.filename,
                "content_type": pdf_artifact.content_type,
                "size_bytes": pdf_artifact.size_bytes,
            })

            success = True
        except RuntimeError:
            success = False
        except Exception as e:
            success = False
            if error_message is None:
                error_message = f"{type(e).__name__}: {e}"
        finally:
            # Always wipe the aux subdir — the final .tex and .pdf are
            # already copied to conv-root with unique names.
            try:
                shutil.rmtree(work_subdir, ignore_errors=True)
            except Exception:
                pass
            await isolation.pool.release(conversation_id)

    return LatexResult(
        success=success,
        tex_artifact=tex_artifact,
        pdf_artifact=pdf_artifact if success else None,
        errors=errors,
        warnings=warnings,
        log_tail=log_tail,
        stdout_tail=stdout_tail,
        duration_ms=int((time.monotonic() - started) * 1000),
        timed_out=timed_out,
        error_message=error_message,
    )
