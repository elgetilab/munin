"""
Download bundles: one zip holding a selection of a conversation's artifacts,
the input files, the code that produced them and the environment it ran in.

Layout (README.txt in the zip says the same):

    MANIFEST.json      every file below with sha256, size, artifact id, version
    SHA256SUMS         the same checksums, for `sha256sum -c SHA256SUMS`
    README.txt
    analysis.py        all successful runs up to the last selected artifact,
                       in order; run from this directory to regenerate outputs
    code/run_NNN.py    every run (including failed ones), as executed
    inputs/...         the uploaded inputs, byte-identical
    outputs/...        the selected artifacts (several versions of one file
                       are named name.vN.ext)
    figures/...json    figure descriptions, where a figure had one
    environment.txt    Python version and installed package versions

All bytes come from the immutable blob store, so a bundle always contains
exactly what each artifact link serves.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import tempfile
import time
import zipfile
from importlib import metadata
from typing import Optional

from .store import ConversationStore

BUNDLE_MAX_BYTES = int(os.environ.get("SANDBOX_BUNDLE_MAX_BYTES", str(1536 * 1024 ** 2)))

_ENVIRONMENT: Optional[str] = None

_README = """Munin analysis bundle

outputs/        the files you selected, exactly as produced
inputs/         the input files the analysis read, exactly as uploaded
analysis.py     the code of every successful run, in order. To reproduce the
                outputs, run it from this directory:  python analysis.py
code/           every run as it was executed, including failed ones
figures/        machine-readable descriptions of the plotted figures
environment.txt Python and package versions of the sandbox
MANIFEST.json   sha256, size and origin of every file
SHA256SUMS      verify the bundle with:  sha256sum -c SHA256SUMS
"""


class BundleError(ValueError):
    pass


def environment_text() -> str:
    global _ENVIRONMENT
    if _ENVIRONMENT is None:
        pkgs = sorted(
            f"{d.metadata['Name']}=={d.version}"
            for d in metadata.distributions()
            if d.metadata.get("Name")
        )
        _ENVIRONMENT = (f"python=={platform.python_version()}\n"
                        f"platform=={platform.platform()}\n\n" + "\n".join(pkgs) + "\n")
    return _ENVIRONMENT


def _versioned(filename: str, version: int) -> str:
    stem, ext = os.path.splitext(filename)
    return f"{stem}.v{version}{ext}"


def build_bundle(store: ConversationStore, artifact_ids: Optional[list[str]],
                 latex_entries: list[dict]) -> str:
    """Write the bundle to a temp file under the store's meta dir and return
    its path. The caller streams and deletes it."""
    entries = [e for e in store.manifest if e.get("sha256")]
    entries += [dict(e, kind="latex") for e in latex_entries if e.get("sha256")]
    if artifact_ids is not None:
        wanted = set(artifact_ids)
        entries = [e for e in entries if e.get("id") in wanted]
        missing = wanted - {e["id"] for e in entries}
        if missing:
            raise BundleError(f"unknown artifact ids: {sorted(missing)[:5]}")
    if not entries and not store.inputs:
        raise BundleError("nothing to bundle")
    store._reload_inputs()
    total = sum(int(e.get("size_bytes") or 0) for e in entries)
    total += sum(int(e.get("size_bytes") or 0) for e in store.inputs)
    if total > BUNDLE_MAX_BYTES:
        raise BundleError(f"selection is {total // 1024 ** 2} MB, over the "
                          f"{BUNDLE_MAX_BYTES // 1024 ** 2} MB bundle limit")

    counts: dict[str, int] = {}
    for e in entries:
        counts[e["filename"]] = counts.get(e["filename"], 0) + 1

    manifest = {"created_at": time.time(), "outputs": [], "inputs": [], "runs": []}
    sums: list[tuple[str, str]] = []

    fd, tmp = tempfile.mkstemp(prefix=".bundle-", suffix=".zip", dir=store.meta)
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            def add_bytes(arcname: str, data: bytes) -> None:
                zf.writestr(arcname, data)
                sums.append((hashlib.sha256(data).hexdigest(), arcname))

            for e in entries:
                fname = e["filename"]
                version = int(e.get("version") or 1)
                arc = "outputs/" + (_versioned(fname, version) if counts[fname] > 1 else fname)
                bfd = store.open_blob(e["sha256"])
                if bfd is None:
                    raise BundleError(f"artifact {e['id']} is missing from the store")
                h = hashlib.sha256()
                with os.fdopen(bfd, "rb") as src, zf.open(arc, "w", force_zip64=True) as dst:
                    while chunk := src.read(1 << 20):
                        h.update(chunk)
                        dst.write(chunk)
                digest = h.hexdigest()
                if digest != e["sha256"]:
                    raise BundleError(f"artifact {e['id']} failed its checksum")
                sums.append((digest, arc))
                rec = {
                    "path": arc, "artifact_id": e["id"], "filename": fname,
                    "version": version, "sha256": digest,
                    "size_bytes": e.get("size_bytes"), "content_type": e.get("content_type"),
                    "kind": e.get("kind", "file"), "exec_seq": e.get("exec_seq"),
                    "code_sha": e.get("code_sha"),
                }
                if e.get("figure"):
                    fig_arc = "figures/" + arc[len("outputs/"):] + ".json"
                    add_bytes(fig_arc, json.dumps(e["figure"], indent=1).encode())
                    rec["figure"] = fig_arc
                manifest["outputs"].append(rec)

            for inp in store.inputs:
                data = store.input_bytes(inp["path"])
                if data is None:
                    continue
                arc = "inputs/" + inp["path"]
                add_bytes(arc, data)
                manifest["inputs"].append({"path": arc, "sha256": inp["sha256"],
                                           "size_bytes": inp["size_bytes"]})

            # Runs up to the newest one that produced a selected artifact
            # (all runs when only inputs or LaTeX were selected).
            seqs = [e.get("exec_seq") for e in entries if e.get("exec_seq")]
            last = max(seqs) if seqs else (store.execs[-1]["seq"] if store.execs else 0)
            script_parts = [
                "# Reproduces the outputs of a Munin conversation.\n"
                "# Successful runs, in the order they were executed. Run from the\n"
                "# bundle directory so relative paths such as inputs/ resolve.\n"
            ]
            for rec in store.execs:
                if rec["seq"] > last:
                    break
                code = store.code_for(rec.get("code_sha", "")) or ""
                status = ("ok" if rec.get("ok") else
                          "timed out" if rec.get("timed_out") else "failed")
                produced = [a["filename"] for a in store.manifest
                            if a.get("id") in set(rec.get("artifacts") or [])]
                header = f"# --- run {rec['seq']} ({status})"
                if produced:
                    header += f", produced: {', '.join(produced[:10])}"
                header += " ---\n"
                add_bytes(f"code/run_{rec['seq']:03d}.py", (header + code).encode())
                manifest["runs"].append({"seq": rec["seq"], "status": status,
                                         "code_sha": rec.get("code_sha"),
                                         "path": f"code/run_{rec['seq']:03d}.py"})
                if rec.get("ok"):
                    script_parts.append("\n" + header + code.rstrip("\n") + "\n")
            add_bytes("analysis.py", "".join(script_parts).encode())
            add_bytes("environment.txt", environment_text().encode())
            add_bytes("README.txt", _README.encode())
            add_bytes("MANIFEST.json", json.dumps(manifest, indent=1).encode())
            zf.writestr("SHA256SUMS", "".join(f"{d}  {a}\n" for d, a in sums))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return tmp
