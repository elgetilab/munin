"""S1 NIST StRD nonlinear regression cases.

The 27 reference files (public domain, NIST/ITL) are kept verbatim under
fixtures/lab/nist/raw/. They carry the certified answers, so they are never
uploaded: each case gets a data-only CSV with the x and y strings copied
exactly, and the prompt states the model and the first starting values.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from common import write_case

RAW = Path(__file__).resolve().parent.parent / "nist" / "raw"


def parse(path: Path) -> dict:
    lines = path.read_text(errors="replace").splitlines()
    text = "\n".join(lines)
    name = re.search(r"Dataset Name:\s+(\S+)", text).group(1)
    level = re.search(r"(Lower|Average|Higher) Level of Difficulty", text).group(1).lower()
    # model: from the "N Parameters" line to the starting-values header
    i0 = next(i for i, l in enumerate(lines) if re.search(r"\d+ Parameters", l))
    i1 = next(i for i, l in enumerate(lines) if i > i0 and "starting values" in l.lower())
    model_lines = [l.strip() for l in lines[i0 + 1:i1] if l.strip()]
    model = " ".join(model_lines).replace("  ", " ")
    model = re.sub(r"\s*\+\s*e\s*$", "", model)
    params, start1, start2, cert, cert_sd = [], [], [], {}, {}
    for l in lines:
        m = re.match(r"\s*(b\d+)\s*=\s*(\S+)\s+(\S+)\s+(\S+)\s+(\S+)", l)
        if m:
            b = m.group(1)
            params.append(b)
            start1.append(m.group(2))
            start2.append(m.group(3))
            cert[b] = float(m.group(4))
            cert_sd[b] = float(m.group(5))
    rss = float(re.search(r"Residual Sum of Squares:\s+(\S+)", text).group(1))
    hdr = next(i for i, l in enumerate(lines) if re.match(r"Data:\s+[xy]\d?\s+[xy]", l))
    order = lines[hdr].split()[1:]
    columns = sorted((c for c in order if c != "y")) + ["y"]   # predictors first, then y
    rows = []
    for l in lines[hdr + 1:]:
        parts = l.split()
        if len(parts) == len(order):
            rec = dict(zip(order, parts))
            rows.append([rec[c] for c in columns])
    return {"name": name, "level": level, "model": model, "params": params, "start1": start1,
            "start2": start2, "certified": cert, "certified_sd": cert_sd, "rss": rss, "columns": columns, "rows": rows}


def build(root: Path) -> None:
    for path in sorted(RAW.glob("*.dat")):
        d = parse(path)
        data = (",".join(d["columns"]) + "\n" + "".join(",".join(r) + "\n" for r in d["rows"])).encode()
        fname = f"{d['name'].lower()}.csv"
        starts = ", ".join(f"{p} = {s}" for p, s in zip(d["params"], d["start1"]))
        prompt = (f"Fit the nonlinear model {d['model']} to the data in {fname} (columns {', '.join(d['columns'])}) by "
                  f"least squares, starting from {starts}. Report every parameter "
                  f"({', '.join(d['params'])}) to at least 6 significant digits and the residual sum "
                  "of squares as rss.")
        checks = [{"type": "numeric", "key": p, "truth": f"truth:certified.{p}", "rtol": 1e-4}
                  for p in d["params"]]
        # Lanczos1 is exact data (RSS ~1e-25): below double precision noise a
        # relative check is meaningless, so a tiny absolute floor applies.
        checks.append({"type": "numeric", "key": "rss", "truth": "truth:rss", "rtol": 1e-4,
                       "atol": 1e-15 if d["rss"] < 1e-15 else 0})
        truth = {"certified": d["certified"], "certified_sd": d["certified_sd"], "rss": d["rss"],
                 "level": d["level"]}
        cid = f"s1_nist_{d['name'].lower()}"
        write_case(root, cid, "S1", [{"prompt": prompt, "checks": checks}], files={fname: data},
                   results_keys={k: "" for k in [*d["params"], "rss"]}, truth=None,
                   tags=["nist", f"nist:{d['level']}"],
                   provenance=f"NIST StRD {d['name']} (public domain), data copied verbatim")
        # certified values keep all their digits (no 6-digit rounding)
        (root / cid / "truth.json").write_text(json.dumps(truth, indent=1, sort_keys=True) + "\n")
