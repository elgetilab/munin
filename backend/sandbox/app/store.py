"""
Per-conversation artifact store, input files and execution log.

Everything here lives under isolation.META_ROOT/{cid}/, which kernels cannot
read or write. The kernel's scratch dir is only ever READ (without following
links) to pick up what user code wrote.

Artifacts are immutable and content-addressed:

- After each execution the scratch dir is walked recursively. A file whose
  bytes differ from the last version seen at that path becomes a NEW artifact
  (new id, version n+1); its bytes are copied into ``blobs/{sha256}``. Earlier
  versions keep their own blobs, so an old link always serves the bytes it
  served when it was created. (Before 2026-11 artifacts were keyed by
  filename: re-saving ``fit.png`` surfaced nothing and the old link silently
  served the new bytes.)
- Inline images (plt.show) are written straight to the blob store.
- Each artifact records the sha256 and the hash of the code that produced it;
  the code itself is kept in ``code/{sha256}.py`` and every execution is
  logged, so a bundle can ship the script that reproduces its files.

Input files (user uploads) are kept canonically in ``inputs/`` here and
mirrored, root-owned and read-only, into ``/scratch/{cid}/inputs/`` before
every execution, so user code can read them but never alter them.
"""

from __future__ import annotations

import hashlib
import io
import json
import mimetypes
import os
import stat
import time
import uuid
import zipfile
from typing import Iterable, Optional

from . import isolation

MANIFEST_FILENAME = "_artifacts.json"
EXECS_FILENAME = "_execs.json"
INPUTS_FILENAME = "_inputs.json"
INPUTS_DIRNAME = "inputs"
LATEX_MANIFEST_FILENAME = "_latex_artifacts.json"

STORE_MAX_BYTES = int(os.environ.get("SANDBOX_STORE_MAX_BYTES", str(2 * 1024 ** 3)))
INPUT_MAX_BYTES = int(os.environ.get("SANDBOX_INPUT_MAX_BYTES", str(100 * 1024 ** 2)))
INPUTS_TOTAL_MAX_BYTES = int(os.environ.get("SANDBOX_INPUTS_TOTAL_MAX_BYTES", str(1024 ** 3)))
ZIP_MAX_MEMBERS = 5000
SCAN_MAX_DEPTH = 6
SCAN_MAX_FILES = 5000
MAX_NEW_PER_EXEC = 200
FIGURE_JSON_MAX_BYTES = 512 * 1024

_SKIP_DIRS = {"__pycache__", INPUTS_DIRNAME}


class InputError(ValueError):
    """An upload was refused. The message is safe to show the user."""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_json(path: str, data) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def _load_json(path: str, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return default


def safe_name(name: str) -> str:
    """A single path component for an uploaded file."""
    name = os.path.basename((name or "").replace("\\", "/")).strip()
    name = "".join(ch for ch in name if ch >= " " and ch != "\x7f")
    if name in ("", ".", "..") or name.startswith("."):
        name = "upload" + (name if name.startswith(".") and len(name) > 1 else "")
    return name[:200]


def _safe_relpath(rel: str) -> Optional[str]:
    """A relative path with no absolute, parent or hidden components, or None."""
    parts = [p for p in rel.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." or p.startswith(".") for p in parts):
        return None
    parts = [safe_name(p) for p in parts]
    return "/".join(parts)


class ConversationStore:
    def __init__(self, conversation_id: str, scratch_dir: str) -> None:
        self.cid = conversation_id
        self.scratch_dir = scratch_dir
        self.meta = isolation.meta_dir(conversation_id)
        self.blob_dir = self._mkdir("blobs")
        self.code_dir = self._mkdir("code")
        self.inputs_dir = self._mkdir(INPUTS_DIRNAME)
        self.manifest_path = os.path.join(self.meta, MANIFEST_FILENAME)
        self.execs_path = os.path.join(self.meta, EXECS_FILENAME)
        self.inputs_path = os.path.join(self.meta, INPUTS_FILENAME)
        self.manifest: list[dict] = [
            e for e in _load_json(self.manifest_path, []) if isinstance(e, dict)
        ]
        self.execs: list[dict] = [
            e for e in _load_json(self.execs_path, []) if isinstance(e, dict)
        ]
        self.inputs: list[dict] = [
            e for e in _load_json(self.inputs_path, []) if isinstance(e, dict)
        ]
        # path -> {"size", "mtime_ns", "sha256", "id"} for the latest version
        self._paths: dict[str, dict] = {}
        for e in self.manifest:
            if e.get("kind", "file") != "file":
                continue
            fname = e.get("filename")
            if not fname:
                continue
            state = {"size": e.get("size_bytes"), "mtime_ns": e.get("mtime_ns"),
                     "sha256": e.get("sha256"), "id": e.get("id")}
            if e.get("sha256") is None:
                # Entry from before the content-addressed store: take the file
                # as it is now as the baseline, so the first scan after the
                # upgrade does not re-surface every old file.
                st = self._lstat_rel(fname)
                if st is not None:
                    state.update(size=st.st_size, mtime_ns=st.st_mtime_ns)
            self._paths[fname] = state

    def _mkdir(self, name: str) -> str:
        d = os.path.join(self.meta, name)
        os.makedirs(d, mode=0o700, exist_ok=True)
        return d

    def _lstat_rel(self, rel: str):
        try:
            st = os.lstat(os.path.join(self.scratch_dir, rel))
        except OSError:
            return None
        return st if stat.S_ISREG(st.st_mode) else None

    # ---- persistence -----------------------------------------------------

    def _save_manifest(self) -> None:
        try:
            _atomic_json(self.manifest_path, self.manifest)
        except OSError:
            pass

    def _save_execs(self) -> None:
        try:
            _atomic_json(self.execs_path, self.execs)
        except OSError:
            pass

    def _save_inputs(self) -> None:
        try:
            _atomic_json(self.inputs_path, self.inputs)
        except OSError:
            pass

    def store_bytes(self) -> int:
        total = 0
        try:
            with os.scandir(self.blob_dir) as it:
                for entry in it:
                    if entry.is_file(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_size
        except OSError:
            pass
        return total

    # ---- execution log ---------------------------------------------------

    def begin_exec(self, code: str) -> dict:
        code_bytes = code.encode("utf-8", errors="replace")
        code_sha = _sha256_bytes(code_bytes)
        path = os.path.join(self.code_dir, code_sha + ".py")
        if not os.path.exists(path):
            try:
                with open(path, "wb") as f:
                    f.write(code_bytes)
            except OSError:
                pass
        rec = {
            "seq": (self.execs[-1]["seq"] + 1) if self.execs else 1,
            "code_sha": code_sha,
            "started_at": time.time(),
        }
        self.execs.append(rec)
        self._save_execs()
        return rec

    def end_exec(self, rec: dict, *, ok: bool, timed_out: bool, duration_ms: int,
                 artifact_ids: Iterable[str]) -> None:
        rec.update(ok=ok, timed_out=timed_out, duration_ms=duration_ms,
                   artifacts=list(artifact_ids))
        self._save_execs()

    def code_for(self, code_sha: str) -> Optional[str]:
        if not code_sha or not all(c in "0123456789abcdef" for c in code_sha):
            return None
        try:
            with open(os.path.join(self.code_dir, code_sha + ".py"), encoding="utf-8",
                      errors="replace") as f:
                return f.read()
        except OSError:
            return None

    # ---- blobs -----------------------------------------------------------

    def _put_blob_bytes(self, data: bytes) -> str:
        sha = _sha256_bytes(data)
        dst = os.path.join(self.blob_dir, sha)
        if not os.path.exists(dst):
            tmp = dst + f".tmp{uuid.uuid4().hex}"
            with open(tmp, "wb") as f:
                f.write(data)
            os.chmod(tmp, 0o400)
            os.replace(tmp, dst)
        return sha

    def _put_blob_fd(self, fd: int) -> tuple[str, int]:
        """Copy an open file into the blob store, hashing on the way."""
        h = hashlib.sha256()
        tmp = os.path.join(self.blob_dir, f".tmp{uuid.uuid4().hex}")
        size = 0
        try:
            with open(tmp, "wb") as out:
                while True:
                    chunk = os.read(fd, 1 << 20)
                    if not chunk:
                        break
                    h.update(chunk)
                    out.write(chunk)
                    size += len(chunk)
            sha = h.hexdigest()
            dst = os.path.join(self.blob_dir, sha)
            if os.path.exists(dst):
                os.unlink(tmp)
            else:
                os.chmod(tmp, 0o400)
                os.replace(tmp, dst)
            return sha, size
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def blob_from_file(self, dir_path: str, name: str) -> Optional[str]:
        """Copy a regular file (never a link) into the blob store; its sha."""
        fd = isolation.open_regular_nofollow(dir_path, name)
        if fd is None:
            return None
        try:
            return self._put_blob_fd(fd)[0]
        except OSError:
            return None
        finally:
            os.close(fd)

    def open_blob(self, sha: str) -> Optional[int]:
        if not sha or len(sha) != 64 or not all(c in "0123456789abcdef" for c in sha):
            return None
        return isolation.open_regular_nofollow(self.blob_dir, sha)

    # ---- artifacts -------------------------------------------------------

    def _versions_of(self, filename: str) -> int:
        return sum(1 for e in self.manifest if e.get("filename") == filename)

    def _register(self, entry: dict) -> dict:
        self.manifest.append(entry)
        self._save_manifest()
        return entry

    def add_inline_image(self, png: bytes, exec_rec: Optional[dict],
                         figure: Optional[dict]) -> Optional[dict]:
        if self.store_bytes() + len(png) > STORE_MAX_BYTES:
            return None
        sha = self._put_blob_bytes(png)
        seq = exec_rec["seq"] if exec_rec else 0
        n = sum(1 for e in self.manifest if e.get("kind") == "inline" and e.get("exec_seq") == seq)
        entry = {
            "id": uuid.uuid4().hex,
            "kind": "inline",
            "filename": f"plot_{seq:03d}_{n + 1}.png",
            "content_type": "image/png",
            "size_bytes": len(png),
            "sha256": sha,
            "version": 1,
            "exec_seq": seq,
            "code_sha": exec_rec["code_sha"] if exec_rec else None,
            "created_at": time.time(),
        }
        _attach_figure(entry, figure)
        return self._register(entry)

    def scan(self, exec_rec: Optional[dict], figures: dict[str, dict]) -> tuple[list[dict], list[str]]:
        """Surface files written or changed in the scratch dir as new artifacts.

        ``figures`` maps absolute paths to figure descriptions published by
        the in-kernel hook during this execution.
        """
        new: list[dict] = []
        warnings: list[str] = []
        # compile_latex outputs are registered by latex.py in their own manifest.
        latex_names = {
            e.get("filename") for e in _load_json(os.path.join(self.meta, LATEX_MANIFEST_FILENAME), [])
            if isinstance(e, dict)
        }
        budget = STORE_MAX_BYTES - self.store_bytes()
        seen = 0
        try:
            walker = os.fwalk(self.scratch_dir, follow_symlinks=False)
        except OSError:
            return new, warnings
        for dirpath, dirnames, filenames, dirfd in walker:
            rel_dir = os.path.relpath(dirpath, self.scratch_dir)
            depth = 0 if rel_dir == "." else rel_dir.count(os.sep) + 1
            dirnames[:] = sorted(
                d for d in dirnames
                if not d.startswith(".") and not (depth == 0 and d in _SKIP_DIRS)
                and d != "__pycache__" and depth < SCAN_MAX_DEPTH
                and not (depth == 0 and d.startswith("latex-"))
            )
            for fname in sorted(filenames):
                if fname.startswith("."):
                    continue
                seen += 1
                if seen > SCAN_MAX_FILES:
                    warnings.append(f"only the first {SCAN_MAX_FILES} files were checked for changes")
                    return new, warnings
                rel = fname if rel_dir == "." else os.path.join(rel_dir, fname)
                if rel in latex_names:
                    continue
                try:
                    st = os.stat(fname, dir_fd=dirfd, follow_symlinks=False)
                except OSError:
                    continue
                if not stat.S_ISREG(st.st_mode):
                    continue
                prev = self._paths.get(rel)
                if prev and prev.get("size") == st.st_size and prev.get("mtime_ns") == st.st_mtime_ns:
                    continue
                if len(new) >= MAX_NEW_PER_EXEC:
                    warnings.append(f"more than {MAX_NEW_PER_EXEC} new files in one run; "
                                    "the rest were not surfaced")
                    return new, warnings
                if st.st_size > budget:
                    warnings.append(f"{rel} was not saved as an artifact: the "
                                    "conversation's artifact storage is full")
                    continue
                try:
                    fd = os.open(fname, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dirfd)
                except OSError:
                    continue
                try:
                    fst = os.fstat(fd)
                    if not stat.S_ISREG(fst.st_mode):
                        continue
                    sha, size = self._put_blob_fd(fd)
                except OSError:
                    continue
                finally:
                    os.close(fd)
                if prev and prev.get("sha256") == sha:
                    prev.update(size=size, mtime_ns=st.st_mtime_ns)
                    continue
                budget -= size
                ctype, _ = mimetypes.guess_type(fname)
                entry = {
                    "id": uuid.uuid4().hex,
                    "kind": "file",
                    "filename": rel,
                    "content_type": ctype or "application/octet-stream",
                    "size_bytes": size,
                    "sha256": sha,
                    "mtime_ns": st.st_mtime_ns,
                    "version": self._versions_of(rel) + 1,
                    "previous_id": prev.get("id") if prev else None,
                    "exec_seq": exec_rec["seq"] if exec_rec else None,
                    "code_sha": exec_rec["code_sha"] if exec_rec else None,
                    "created_at": time.time(),
                }
                fig = figures.get(os.path.join(self.scratch_dir, rel))
                _attach_figure(entry, fig)
                self._register(entry)
                self._paths[rel] = {"size": size, "mtime_ns": st.st_mtime_ns,
                                    "sha256": sha, "id": entry["id"]}
                new.append(entry)
        return new, warnings

    def find(self, artifact_id: str) -> Optional[dict]:
        return next((e for e in self.manifest if e.get("id") == artifact_id), None)

    # ---- inputs ----------------------------------------------------------

    def _reload_inputs(self) -> None:
        # Uploads arrive through a separate request (and store instance) while
        # the kernel's instance lives on, so always read the list from disk.
        self.inputs = [e for e in _load_json(self.inputs_path, []) if isinstance(e, dict)]

    def _inputs_total(self) -> int:
        return sum(int(e.get("size_bytes") or 0) for e in self.inputs)

    def add_input(self, name: str, data: bytes, *, extract_zip: bool = True) -> list[dict]:
        """Store an uploaded file (or the members of an uploaded zip)."""
        self._reload_inputs()
        if len(data) > INPUT_MAX_BYTES:
            raise InputError(f"file is larger than the {INPUT_MAX_BYTES // 1024 ** 2} MB limit")
        name = safe_name(name)
        if extract_zip and name.lower().endswith(".zip") and zipfile.is_zipfile(io.BytesIO(data)):
            return self._add_zip(name, data)
        return [self._add_one(name, data, origin=None)]

    def _add_one(self, rel: str, data: bytes, origin: Optional[str]) -> dict:
        existing = next((e for e in self.inputs if e["path"] == rel), None)
        total = self._inputs_total() - (int(existing["size_bytes"]) if existing else 0)
        if total + len(data) > INPUTS_TOTAL_MAX_BYTES:
            raise InputError("this conversation's input storage is full")
        dst = os.path.join(self.inputs_dir, rel)
        os.makedirs(os.path.dirname(dst), mode=0o700, exist_ok=True)
        tmp = dst + f".tmp{uuid.uuid4().hex}"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, dst)
        ctype, _ = mimetypes.guess_type(rel)
        rec = {"path": rel, "size_bytes": len(data), "sha256": _sha256_bytes(data),
               "content_type": ctype or "application/octet-stream",
               "origin": origin, "added_at": time.time()}
        self.inputs = [e for e in self.inputs if e["path"] != rel] + [rec]
        self._save_inputs()
        return rec

    def _add_zip(self, name: str, data: bytes) -> list[dict]:
        base = os.path.splitext(name)[0] or "upload"
        out: list[dict] = []
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            members = [m for m in zf.infolist() if not m.is_dir()]
            if len(members) > ZIP_MAX_MEMBERS:
                raise InputError(f"zip has more than {ZIP_MAX_MEMBERS} files")
            declared = sum(m.file_size for m in members)
            if declared + self._inputs_total() > INPUTS_TOTAL_MAX_BYTES:
                raise InputError("zip contents exceed this conversation's input storage")
            for m in members:
                # Symlinks inside zips carry S_IFLNK in the high bits.
                if stat.S_ISLNK((m.external_attr >> 16) & 0xFFFF):
                    continue
                inner = _safe_relpath(m.filename)
                if inner is None or inner.split("/")[0] == "__MACOSX":
                    continue
                with zf.open(m) as src:
                    blob = src.read(m.file_size + 1)
                if len(blob) > m.file_size:
                    raise InputError("zip member is larger than it declares")
                out.append(self._add_one(f"{base}/{inner}", blob, origin=name))
        if not out:
            raise InputError("zip contained no usable files")
        return out

    def sync_inputs(self) -> None:
        """Mirror canonical inputs into /scratch/{cid}/inputs, root-owned and
        read-only. Called before every execution, so anything the kernel
        renamed, replaced or chowned is put back."""
        self._reload_inputs()
        if not self.inputs:
            return
        try:
            sfd = os.open(self.scratch_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError:
            return
        try:
            dfd = _root_dir_at(sfd, INPUTS_DIRNAME)
            if dfd is None:
                return
            try:
                for rec in self.inputs:
                    _mirror_file(dfd, rec["path"], os.path.join(self.inputs_dir, rec["path"]),
                                 rec["size_bytes"])
            finally:
                os.close(dfd)
        finally:
            os.close(sfd)

    def input_bytes(self, rel: str) -> Optional[bytes]:
        self._reload_inputs()
        if not any(e["path"] == rel for e in self.inputs):
            return None
        try:
            with open(os.path.join(self.inputs_dir, rel), "rb") as f:
                return f.read()
        except OSError:
            return None


# ---- root-owned mirror helpers ------------------------------------------

def _root_dir_at(parent_fd: int, name: str) -> Optional[int]:
    """fd of a root-owned 0755 directory `name` under parent_fd, (re)created
    if the kernel removed it or replaced it with something else."""
    try:
        st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != 0:
            os.rename(name, f"{name}.moved-{uuid.uuid4().hex[:8]}",
                      src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            raise FileNotFoundError
    except FileNotFoundError:
        try:
            os.mkdir(name, 0o755, dir_fd=parent_fd)
        except OSError:
            return None
    except OSError:
        return None
    try:
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    except OSError:
        return None
    try:
        os.fchown(fd, 0, 0)
        os.fchmod(fd, 0o755)
    except OSError:
        pass
    return fd


def _mirror_file(root_fd: int, rel: str, src: str, size: int) -> None:
    parts = rel.split("/")
    fds: list[int] = []
    cur = root_fd
    try:
        for part in parts[:-1]:
            nxt = _root_dir_at(cur, part)
            if nxt is None:
                return
            fds.append(nxt)
            cur = nxt
        leaf = parts[-1]
        try:
            st = os.stat(leaf, dir_fd=cur, follow_symlinks=False)
            if (stat.S_ISREG(st.st_mode) and st.st_uid == 0 and st.st_size == size
                    and stat.S_IMODE(st.st_mode) == 0o444):
                return
            os.unlink(leaf, dir_fd=cur)
        except FileNotFoundError:
            pass
        except OSError:
            return
        try:
            with open(src, "rb") as f:
                data = f.read()
        except OSError:
            return
        fd = os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444,
                     dir_fd=cur)
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            os.fchown(out.fileno(), 0, 0)
            os.fchmod(out.fileno(), 0o444)
    finally:
        for fd in fds:
            os.close(fd)


# ---- figure descriptions --------------------------------------------------

def _attach_figure(entry: dict, figure: Optional[dict]) -> None:
    if not figure:
        return
    try:
        raw = json.dumps(figure)
    except (TypeError, ValueError):
        return
    if len(raw) > FIGURE_JSON_MAX_BYTES:
        figure = _shrink_figure(figure)
    entry["figure"] = figure
    entry["figure_summary"] = summarize_figure(figure)


def _shrink_figure(fig: dict) -> dict:
    """Drop per-point arrays when a description is too large to keep."""
    out = dict(fig)
    axes = []
    for ax in fig.get("axes") or []:
        ax = dict(ax)
        series = []
        for s in ax.get("series") or []:
            s = dict(s)
            for k, v in list(s.items()):
                if isinstance(v, list) and len(v) > 50:
                    s[k] = {"n": len(v), "dropped": True}
            series.append(s)
        ax["series"] = series
        axes.append(ax)
    out["axes"] = axes
    out["shrunk"] = True
    return out


def _fmt_num(v) -> str:
    if v is None:
        return "?"
    return f"{v:.4g}"


def summarize_figure(fig: dict) -> str:
    """A few lines the model reads after each run to check its own plot."""
    lines = []
    size = fig.get("size_mm") or [None, None]
    head = f"{_fmt_num(size[0])}x{_fmt_num(size[1])} mm"
    if fig.get("dpi"):
        head += f", {_fmt_num(fig['dpi'])} dpi"
    if fig.get("format"):
        head += f", {fig['format']}"
    axes = fig.get("axes") or []
    head += f", {len(axes)} axes"
    if fig.get("suptitle"):
        head += f", suptitle {fig['suptitle']!r}"
    lines.append(head)
    for i, ax in enumerate(axes):
        if "error" in ax:
            lines.append(f"axes {i}: could not be described ({ax['error']})")
            continue
        parts = [f"x {ax.get('xscale')} {_lim(ax.get('xlim'))}"
                 + (f" {ax['xlabel']!r}" if ax.get("xlabel") else " (no label)"),
                 f"y {ax.get('yscale')} {_lim(ax.get('ylim'))}"
                 + (f" {ax['ylabel']!r}" if ax.get("ylabel") else " (no label)")]
        if ax.get("title"):
            parts.insert(0, f"title {ax['title']!r}")
        lines.append(f"axes {i}: " + "; ".join(parts))
        for s in ax.get("series") or []:
            lines.append("  - " + _series_line(s))
        if ax.get("legend"):
            lines.append(f"  legend: {', '.join(ax['legend'][:12])}")
        if ax.get("texts"):
            lines.append(f"  text: {', '.join(t[:20] for t in ax['texts'][:12])}")
    lay = fig.get("layout") or {}
    probs = []
    if lay.get("clipped_text"):
        probs.append(f"text cut off at the figure edge: {', '.join(lay['clipped_text'][:5])}")
    if lay.get("overlapping_text"):
        probs.append(f"{len(lay['overlapping_text'])} overlapping text pairs, e.g. "
                     f"{lay['overlapping_text'][0]}")
    if lay.get("legend_covers_points"):
        probs.append(f"legend covers {lay['legend_covers_points']} data points")
    lines.append("layout: " + ("; ".join(probs) if probs else "no problems found"))
    return "\n".join(lines)


def _lim(lim) -> str:
    if not lim or len(lim) != 2:
        return ""
    return f"[{_fmt_num(lim[0])}, {_fmt_num(lim[1])}]"


def _n(v) -> int:
    if isinstance(v, list):
        return len(v)
    if isinstance(v, dict):
        return int(v.get("n") or 0)
    return 0


def _series_line(s: dict) -> str:
    kind = s.get("kind", "?")
    label = f" {s['label']!r}" if s.get("label") else ""
    if kind in ("line", "points", "scatter", "errorbar"):
        out = f"{kind}{label}: {_n(s.get('x'))} points"
        if s.get("color"):
            out += f", {s['color']}"
        for k in ("yerr", "yerr_low", "xerr"):
            if k in s:
                out += f", {k.split('_')[0]} bars"
                break
        return out
    if kind == "bar":
        out = f"bars{label}: {_n(s.get('values'))} bars"
        if any(k in s for k in ("yerr", "yerr_low", "xerr", "xerr_low")):
            out += " with error bars"
        return out
    if kind == "contour":
        return f"contour{label}: {_n(s.get('levels'))} levels" + (" (filled)" if s.get("filled") else "")
    if kind == "image":
        return f"image {s.get('shape')} cmap {s.get('cmap')}"
    if kind == "fill":
        return f"filled area{label}"
    if kind == "segments":
        return f"{s.get('n')} line segments{label}"
    return kind
