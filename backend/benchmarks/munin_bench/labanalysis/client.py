"""Drives one case through the live chat API and fills a RunRecord.

Talks to retrieval directly with an identity header and the gateway token
(munin_bench.gateway), as the other eval runners do. Every chat is real
(non-ephemeral, because the sandbox refuses temporary chats) and is deleted
after grading, which also purges its sandbox files (plan D7).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Optional

import httpx

from ..gateway import gateway_headers
from .cases import Case
from .filemeta import file_meta
from .record import Artifact, RunRecord, TurnRecord, results_from_answer

EVAL_USER = "eval-lab@munin.local"
META_EXTS = (".png", ".pdf", ".svg", ".tif", ".tiff")
MAX_FETCH = 64 * 1024 ** 2   # artifacts larger than this are not re-hashed


class LabClient:
    def __init__(self, base: str = "http://127.0.0.1:8080", user: str = EVAL_USER,
                 persona: Optional[str] = "munin", timeout: float = 900.0,
                 egress: str = "off"):
        self.base = base.rstrip("/")
        self.persona = persona
        headers = {"X-Munin-Email": user, **gateway_headers()}
        if egress:
            headers["X-Munin-Egress"] = egress
        self.http = httpx.Client(base_url=self.base, headers=headers,
                                 timeout=httpx.Timeout(timeout, connect=15.0))

    def close(self) -> None:
        self.http.close()

    # -- API -----------------------------------------------------------------

    def upload(self, path: Path) -> dict:
        with open(path, "rb") as fh:
            r = self.http.post("/api/documents/upload",
                               files={"file": (path.name, fh, "application/octet-stream")})
        r.raise_for_status()
        doc = r.json()
        if not doc.get("document_id"):
            raise RuntimeError(f"upload of {path.name} returned {doc}")
        return doc

    def turn(self, prompt: str, cid: Optional[str], docs: list[dict]) -> tuple[Optional[str], TurnRecord]:
        content: list[dict] = [{"type": "text", "text": prompt}]
        content += [{"type": "document", "document_id": d["document_id"], "filename": d["filename"]}
                    for d in docs]
        body: dict = {"stream": True, "messages": [{"role": "user", "content": content}]}
        if self.persona:
            body["persona"] = self.persona
        if cid:
            body["conversation_id"] = cid
        tr = TurnRecord(index=0, prompt=prompt)
        text: list[str] = []
        event = ""
        t0 = time.monotonic()
        with self.http.stream("POST", "/api/chat/completions", json=body) as r:
            if r.status_code != 200:
                r.read()
                raise RuntimeError(f"chat POST {r.status_code}: {r.text[:200]}")
            for line in r.iter_lines():
                if line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    event = line[6:].strip()
                    continue
                if not line.startswith("data:"):
                    if line == "":
                        event = ""
                    continue
                try:
                    d = json.loads(line[5:])
                except ValueError:
                    continue
                if event == "conversation" and d.get("id"):
                    cid = d["id"]
                elif event == "token":
                    text.append(d.get("content") or d.get("text") or "")
                elif event == "tool_call" and d.get("name"):
                    tr.tools.append(d["name"])
                elif event == "error":
                    tr.error = str(d.get("message") or d)[:500]
        tr.seconds = round(time.monotonic() - t0, 1)
        tr.answer = "".join(text)
        return cid, tr

    def persisted_answers(self, cid: str) -> list[str]:
        r = self.http.get(f"/api/chats/{cid}")
        if r.status_code != 200:
            return []
        return [m.get("content") or "" for m in r.json().get("messages") or []
                if m.get("role") == "assistant"]

    def artifacts(self, cid: str) -> list[dict]:
        r = self.http.get(f"/api/artifacts/{cid}")
        if r.status_code != 200:
            return []
        return [a for a in r.json().get("artifacts") or [] if a.get("sha256")]

    def artifact_bytes(self, cid: str, aid: str) -> Optional[bytes]:
        r = self.http.get(f"/api/artifacts/{cid}/{aid}")
        return r.content if r.status_code == 200 else None

    def bundle(self, cid: str) -> Optional[bytes]:
        r = self.http.get(f"/api/bundles/{cid}")
        return r.content if r.status_code == 200 else None

    def delete_chat(self, cid: str) -> None:
        self.http.delete(f"/api/chats/{cid}")

    def delete_doc(self, doc_id: str) -> None:
        self.http.delete(f"/api/documents/{doc_id}")

    # -- one case ------------------------------------------------------------

    def run_case(self, case: Case, rep: int = 1, keep: bool = False,
                 sandbox_image: str = "munin-sandbox:latest") -> tuple[RunRecord, Optional[bytes]]:
        rec = RunRecord(case_id=case.id, suite=case.suite, rep=rep)
        docs: list[dict] = []
        bundle: Optional[bytes] = None
        cid: Optional[str] = None
        seen: set[str] = set()
        try:
            for p in case.input_paths():
                doc = self.upload(p)
                docs.append({"document_id": doc["document_id"], "filename": p.name})
            for i, prompt in enumerate(case.prompts(), start=1):
                cid, tr = self.turn(prompt, cid, docs if i == 1 else [])
                tr.index = i
                rec.conversation_id = cid
                rec.turns.append(tr)
                if not cid:
                    raise RuntimeError("no conversation id from the first turn")
                for a in self.artifacts(cid):
                    if a["id"] in seen:
                        continue
                    seen.add(a["id"])
                    rec.artifacts.append(Artifact(
                        id=a["id"], filename=a["filename"], sha256=a["sha256"],
                        version=int(a.get("version") or 1),
                        content_type=a.get("content_type") or "",
                        size_bytes=int(a.get("size_bytes") or 0), turn=i,
                        figure_summary=a.get("figure_summary")))
                self._results_for_turn(cid, rec, tr)
            if cid:
                answers = self.persisted_answers(cid)
                # Tokens can be lost on a reconnect; the stored answer is canonical.
                if len(answers) >= len(rec.turns):
                    for tr, text in zip(rec.turns, answers[-len(rec.turns):]):
                        if text:
                            tr.answer = text
                for tr in rec.turns:
                    if tr.results is None:
                        found = results_from_answer(tr.answer)
                        if found is not None:
                            tr.results, tr.results_source = found, "answer"
                self._served_and_meta(cid, rec)
                bundle = self.bundle(cid)
                rec.bundle_ok = _attach_bundle(bundle, rec) if bundle else False
                if case.suite == "S5" and bundle:
                    rec.repro = reproduce(bundle, case, sandbox_image)
        except Exception as exc:  # recorded, never raised: the suite goes on
            rec.error = f"{type(exc).__name__}: {exc}"[:500]
        finally:
            if not keep:
                if cid:
                    try:
                        self.delete_chat(cid)
                    except httpx.HTTPError:
                        pass
                for d in docs:
                    try:
                        self.delete_doc(d["document_id"])
                    except httpx.HTTPError:
                        pass
        return rec, bundle

    def _results_for_turn(self, cid: str, rec: RunRecord, tr: TurnRecord) -> None:
        mine = [a for a in rec.artifacts_of(tr.index) if a.filename.endswith("results.json")]
        if not mine:
            return
        data = self.artifact_bytes(cid, mine[-1].id)
        try:
            obj = json.loads(data) if data else None
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            tr.results, tr.results_source = obj, "artifact"

    def _served_and_meta(self, cid: str, rec: RunRecord) -> None:
        for a in rec.artifacts:
            if a.size_bytes > MAX_FETCH:
                continue
            data = self.artifact_bytes(cid, a.id)
            if data is None:
                continue
            a.served_sha256 = hashlib.sha256(data).hexdigest()
            if a.filename.lower().endswith(META_EXTS):
                a.meta = file_meta(a.filename, data)


def _attach_bundle(data: bytes, rec: RunRecord) -> bool:
    """Verify the bundle's SHA256SUMS and copy the full figure descriptions
    into the record (the listing only carries the summary)."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        sums = [line.split("  ", 1) for line in z.read("SHA256SUMS").decode().splitlines() if line]
        ok = bool(sums) and all(hashlib.sha256(z.read(p)).hexdigest() == d for d, p in sums)
        manifest = json.loads(z.read("MANIFEST.json"))
    except (KeyError, ValueError, zipfile.BadZipFile):
        return False
    by_id = {a.id: a for a in rec.artifacts}
    for out in manifest.get("outputs", []):
        art = by_id.get(out.get("artifact_id"))
        if art is not None and out.get("figure"):
            try:
                art.figure = json.loads(z.read(out["figure"]))
            except (KeyError, ValueError):
                pass
    return ok


# -- S5 reproduction ---------------------------------------------------------

def _numbers(text: str) -> list[float]:
    import re
    return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", text)]


def outputs_match(name: str, original: bytes, regenerated: bytes, rtol: float = 1e-6) -> bool:
    """Byte-identical, or for text files the same numbers within rtol, or for
    PNG the same pixels within one grey level."""
    if original == regenerated:
        return True
    low = name.lower()
    if low.endswith((".json", ".csv", ".tsv", ".txt")):
        a, b = _numbers(original.decode(errors="replace")), _numbers(regenerated.decode(errors="replace"))
        return len(a) == len(b) and all(abs(x - y) <= rtol * max(abs(x), abs(y), 1e-12) for x, y in zip(a, b))
    if low.endswith(".png"):
        try:
            import numpy as np
            from PIL import Image
            ia = np.asarray(Image.open(io.BytesIO(original)).convert("RGBA"), dtype=int)
            ib = np.asarray(Image.open(io.BytesIO(regenerated)).convert("RGBA"), dtype=int)
            return ia.shape == ib.shape and int(np.abs(ia - ib).max()) <= 1
        except Exception:
            return False
    return False


def reproduce(bundle: bytes, case: Case, image: str, timeout: int = 600) -> dict:
    """Unpack the bundle, run analysis.py in a fresh, offline sandbox
    container, and compare each regenerated output with the original."""
    work = Path(tempfile.mkdtemp(prefix="lab-repro-"))
    try:
        z = zipfile.ZipFile(io.BytesIO(bundle))
        manifest = json.loads(z.read("MANIFEST.json"))
        run_dir = work / "run"
        run_dir.mkdir()
        for name in z.namelist():
            if name.startswith("inputs/") or name == "analysis.py":
                target = run_dir / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(z.read(name))
        os.chmod(work, 0o777)
        for p in run_dir.rglob("*"):
            os.chmod(p, 0o777 if p.is_dir() else 0o666)
        os.chmod(run_dir, 0o777)
        cmd = ["docker", "run", "--rm", "--network", "none", "--user", f"{os.getuid()}:{os.getgid()}",
               "-e", "HOME=/tmp", "-e", "MPLCONFIGDIR=/tmp", "-e", "MPLBACKEND=Agg",
               "-v", f"{run_dir}:/work", "-w", "/work", "--entrypoint", "python", image, "analysis.py"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        outputs: dict[str, bool] = {}
        for out in manifest.get("outputs", []):
            fname = out["filename"]
            regenerated = run_dir / fname
            if not regenerated.exists():
                outputs[fname] = False
                continue
            # The newest version of each file is what a re-run leaves behind.
            newest = max((o for o in manifest["outputs"] if o["filename"] == fname),
                         key=lambda o: o.get("version") or 1)
            if newest is not out:
                continue
            outputs[fname] = outputs_match(fname, z.read(out["path"]), regenerated.read_bytes())
        return {"ran": proc.returncode == 0, "outputs": outputs,
                "detail": (proc.stderr or "")[-800:]}
    except subprocess.TimeoutExpired:
        return {"ran": False, "outputs": {}, "detail": f"timed out after {timeout}s"}
    except Exception as exc:
        return {"ran": False, "outputs": {}, "detail": f"{type(exc).__name__}: {exc}"}
    finally:
        shutil.rmtree(work, ignore_errors=True)
