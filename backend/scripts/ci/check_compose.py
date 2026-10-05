#!/usr/bin/env python3
"""
==============================================================================
Compose invariant checks
==============================================================================
The single-host setup rests on a few properties that are easy to break with a
careless edit and produce no error when broken, only wrong behaviour:

  1. THE CLUSTER PATH DEFAULTS ARE PRODUCTION, BUT NOTHING ELSE IS. Paths
     still default to /opt/munin, because that is what the deployed file runs
     on. The instance's identity does not: MUNIN_DOMAIN and the secrets are
     required, compose refuses to start without them, the reference values
     resolve exactly as production ran before (2026-10), and with any other
     domain no `muninai.org` survives anywhere in the resolved config.

  2. THE LOCAL OVERRIDES REACH THE WORKING TREE. With .env.example, no path may
     still point at /opt, or a clone would depend on a machine it is not on.

  3. THE PROJECT NAME IS PINNED. Without `name:`, Compose derives the project
     from the directory basename -- `docker` for BOTH /opt/munin/docker and a
     clone's backend/docker -- so a local `compose up` adopts and recreates the
     production stack. That happened on 2026-08-14.

  4. FRONTEND PATHS SURVIVE BEING SECOND IN COMPOSE_FILE. Compose resolves
     relative paths against the FIRST file's directory, so a literal ./caddy in
     the frontend file would resolve into backend/docker/. Every frontend path
     must be parameterised and land under frontend/.

Run from the repo root:  python3 backend/scripts/ci/check_compose.py
==============================================================================
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

BACKEND = "backend/docker/docker-compose.yml"
FRONTEND = "frontend/docker-compose.yml"

failures: list = []
checks = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if ok:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}" + (f"\n         {detail}" if detail else ""))
        failures.append(name)


def compose_config(files: list, env_file: str | None, cwd: str,
                   profiles: list | None = None) -> dict:
    cmd = ["docker", "compose"]
    for f in files:
        cmd += ["-f", f]
    cmd += ["--env-file", env_file or os.devnull]
    for p in profiles or []:
        cmd += ["--profile", p]
    # `--format` is a flag of the `config` subcommand, not of `docker compose`.
    cmd += ["config", "--format", "json"]

    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"compose config failed:\n{r.stderr[:800]}")
    return json.loads(r.stdout)


def sources(cfg: dict) -> list:
    """Every host path the config binds, plus every build context."""
    out = []
    for svc in cfg.get("services", {}).values():
        for v in svc.get("volumes") or []:
            if isinstance(v, dict) and v.get("type") == "bind" and v.get("source"):
                out.append(v["source"])
        b = svc.get("build")
        if isinstance(b, dict) and b.get("context"):
            out.append(b["context"])
    return out


MODES = {
    "all (laptop)": ["--mode", "all", "--domain", "localhost",
                     "--llm-url", "http://host.docker.internal:11434", "--llm-model", "m"],
    "all (server)": ["--mode", "all", "--domain", "lab.example.edu",
                     "--llm-url", "http://gpu:8000", "--llm-model", "m",
                     "--smtp-host", "smtp.example.edu"],
    "backend": ["--mode", "backend", "--domain", "lab.example.edu",
                "--llm-url", "http://gpu:8000", "--llm-model", "m",
                "--vps-host", "vps.lab.example.edu"],
    "frontend": ["--mode", "frontend", "--domain", "lab.example.edu",
                 "--smtp-host", "smtp.example.edu"],
}
EXPECT = {  # services each mode must bring up, and ones it must not
    "all (laptop)": ({"retrieval", "caddy", "munin-auth", "webui-build"}, set()),
    "all (server)": ({"retrieval", "caddy", "munin-auth"}, set()),
    "backend": ({"retrieval", "qdrant", "tunnel"}, {"caddy", "munin-auth"}),
    "frontend": ({"caddy", "munin-auth", "api-gateway"}, {"retrieval", "qdrant"}),
}
# Ports the frontend's host-network services listen on (set in their
# Dockerfiles or commands, invisible to `compose config`).
HOST_NETWORK_PORTS = {80: "caddy", 443: "caddy", 8081: "caddy (local)",
                      8082: "caddy (local)", 8083: "caddy (local)",
                      8070: "api-gateway", 11080: "tusd", 18088: "hook-service"}
SHARED = ("ADMIN_INGEST_TOKEN", "KB_GATE_TOKEN", "CONTRIBUTORS_SYNC_TOKEN",
          "MUNIN_GATEWAY_TOKEN")


def _env_values(path: str) -> dict:
    out = {}
    for line in open(path):
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.rstrip("\n").split("=", 1)
            out[k] = v
    return out


def check_modes(root: str) -> None:
    """Run configure.sh for each mode in a copy of the tracked tree (so
    gitignored local files cannot hide a missing one), then resolve it."""
    files = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True,
                           text=True).stdout.split()
    files.append("scripts/configure.sh")
    peer = None
    scratch = tempfile.mkdtemp(prefix="munin-modes-")
    try:
        _check_modes(root, files, scratch)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _check_modes(root: str, files: list, scratch: str) -> None:
    peer = None
    for name, args in MODES.items():
        work = tempfile.mkdtemp(prefix="mode-", dir=scratch)
        subprocess.run(["rsync", "-a", "--files-from=-", root + "/", work + "/"],
                       input="\n".join(files), text=True, check=True)
        extra = ["--peer-env", peer] if name == "frontend" and peer else []
        r = subprocess.run(["bash", "scripts/configure.sh", "--non-interactive",
                            "--admin-email", "me@lab.example.edu", *args, *extra],
                           cwd=work, capture_output=True, text=True)
        if r.returncode != 0:
            check(f"{name}: configure.sh succeeds", False, r.stderr[-300:])
            continue
        if name == "backend":
            peer = os.path.join(scratch, "peer.env")
            subprocess.run(["cp", os.path.join(work, "munin-peer.env"), peer], check=True)
        c = subprocess.run(["docker", "compose", "config", "--format", "json"],
                           cwd=work, capture_output=True, text=True)
        if c.returncode != 0:
            check(f"{name}: the written .env resolves", False, c.stderr[-300:])
            continue
        cfg = json.loads(c.stdout)
        svcs = set(cfg["services"])
        need, avoid = EXPECT[name]
        missing = [s for s in sources(cfg)
                   if s.startswith(work) and not s.startswith(work + "/.runtime")
                   and not os.path.exists(s)]
        check(f"{name}: resolves, right services, every mounted file present",
              need <= svcs and not (avoid & svcs) and not missing
              and "muninai" not in c.stdout,
              f"services {sorted(svcs)}, missing {missing[:3]}")
        if name.startswith("all"):
            # Frontend services on the host network listen on fixed host
            # ports that compose does not publish, so it cannot see a clash
            # with a backend port. GROBID's 8070 against the gateway's 8070
            # broke every one-machine install until 2026-10-05.
            published = {int(p["published"]) for svc in cfg["services"].values()
                         for p in svc.get("ports") or [] if p.get("published")}
            clash = published & set(HOST_NETWORK_PORTS)
            check(f"{name}: no published port collides with a host-network listener",
                  not clash, f"{sorted(clash)}: {[HOST_NETWORK_PORTS[p] for p in clash]}")
        if name == "frontend" and peer:
            a = _env_values(peer)
            b = _env_values(os.path.join(work, ".env"))
            check("split install: both halves share the same tokens",
                  all(a.get(k) and a.get(k) == b.get(k) for k in SHARED))


def main() -> int:
    root = os.path.abspath(os.getcwd())
    if not os.path.exists(BACKEND):
        print(f"run me from the repo root (no {BACKEND})", file=sys.stderr)
        return 2

    # --- 1. paths default to production; identity is required ---------------
    print("\ncluster defaults (the reference env, as deploy.sh runs it)")
    tmp = tempfile.mkdtemp()
    secrets = "NEO4J_PASSWORD=x\nSEARXNG_SECRET=x\n"
    envs = {}
    for name, body in (("none", secrets),
                       ("reference", secrets + "MUNIN_DOMAIN=muninai.org\n"),
                       ("other", secrets + "MUNIN_DOMAIN=lab.example.edu\n")):
        envs[name] = os.path.join(tmp, name + ".env")
        with open(envs[name], "w") as f:
            f.write(body)
    try:
        compose_config(["docker-compose.yml"], envs["none"],
                       cwd=os.path.join(root, "backend/docker"), profiles=["rag"])
        check("compose refuses to start without MUNIN_DOMAIN", False,
              "it resolved with no domain set")
    except RuntimeError as e:
        check("compose refuses to start without MUNIN_DOMAIN",
              "MUNIN_DOMAIN" in str(e), str(e)[:200])
    other = compose_config(["docker-compose.yml"], envs["other"],
                           cwd=os.path.join(root, "backend/docker"),
                           profiles=["rag", "pipeline", "monitoring", "gpu"])
    leaked = "muninai" in json.dumps(other)
    check("no reference-deployment value survives another domain", not leaked,
          "grep the resolved config for muninai")
    cfg = compose_config(["docker-compose.yml"], envs["reference"],
                         cwd=os.path.join(root, "backend/docker"),
                         profiles=["rag", "monitoring"])
    renv = cfg["services"]["retrieval"]["environment"]
    check("the reference domain resolves as production did",
          renv.get("MUNIN_PUBLIC_URL") == "https://search.muninai.org"
          and renv.get("CONTRIBUTORS_SYNC_URL")
          == "https://auth.muninai.org/admin/contributors.yaml"
          and renv.get("AUTH_CHECK_ROLE_URL")
          == "https://auth.muninai.org/admin/check-role",
          f"got {renv.get('MUNIN_PUBLIC_URL')!r}")
    srcs = sources(cfg)
    stray = [s for s in srcs if not s.startswith(("/opt/munin", "/opt/cluster"))]
    check("every path resolves under /opt", not stray, f"stray: {stray[:4]}")
    check("project name pinned to 'munin'", cfg.get("name") == "munin",
          f"got {cfg.get('name')!r}")
    check("retrieval build context is the deployed source",
          any(s == "/opt/munin/services/retrieval" for s in srcs))

    # --- 3. the collision that motivated the pin ---------------------------
    # A repo checkout and /opt/munin/docker are both basename `docker`, so
    # without `name:` they would share a project and adopt each other.
    print("\nproject isolation")
    local_cfg = compose_config(["docker-compose.yml"], envs["other"],
                               cwd=os.path.join(root, "backend/docker"))
    check("clone and cluster cannot share a project",
          local_cfg.get("name") == "munin",
          "unset name: means the basename 'docker' is used by BOTH")

    # --- 2. local overrides reach the working tree -------------------------
    print("\nlocal overrides (.env.example)")
    cfg = compose_config([BACKEND, FRONTEND], ".env.example", cwd=root,
                         profiles=["rag", "pipeline", "monitoring", "webui", "seed"])
    srcs = sources(cfg)
    leaked = [s for s in srcs if s.startswith(("/opt/munin", "/opt/cluster"))]
    check("no path still points at /opt", not leaked, f"leaked: {leaked[:4]}")
    check("paths land inside the repo",
          all(s.startswith(root) for s in srcs),
          f"outside: {[s for s in srcs if not s.startswith(root)][:4]}")

    # --- 5. a fresh clone has every file the stack mounts --------------------
    # A bind-mounted file that only exists in someone's working copy (it was
    # gitignored, or never committed) makes Docker create an empty DIRECTORY
    # in its place, and the service fails in a way that names neither. Every
    # repo-side file source must be tracked, or have a tracked `.example`
    # sibling that the setup docs tell you to copy.
    print("\nfresh-clone completeness")
    tracked = set(subprocess.run(["git", "ls-files"], cwd=root, capture_output=True,
                                 text=True).stdout.split("\n"))
    runtime = os.path.join(root, ".runtime")
    # Generated, not committed: the webui profile builds the chat UI here.
    built = {"frontend/static/chat"}
    missing = []
    for src in srcs:
        if src.startswith(runtime) or not src.startswith(root):
            continue
        rel = os.path.relpath(src, root)
        if rel in built or rel in tracked or rel + ".example" in tracked:
            continue
        if any(t.startswith(rel + "/") for t in tracked):  # a tracked directory
            continue
        missing.append(rel)
    check("every mounted repo path is tracked (or has a .example)", not missing,
          f"untracked: {missing[:4]}")
    caddy = next((c for svc in cfg["services"].values()
                  for c in (svc.get("command") or [])
                  if isinstance(c, str) and "Caddyfile" in c), "")
    check("the Caddyfile .env.example selects is tracked",
          f"frontend/caddy/{os.path.basename(caddy)}" in tracked, f"got {caddy!r}")

    # --- 6. the documented LLM names reach the containers -------------------
    print("\nLLM endpoint override")
    probe = os.path.join(root, ".check_compose_llm.env")
    with open(probe, "w") as f:
        f.write("LLM_BASE_URL=http://llm-probe:1234\nLLM_MODEL_NAME=probe-model\n"
                "MUNIN_DOMAIN=x.org\nNEO4J_PASSWORD=x\nSEARXNG_SECRET=x\n")
    try:
        llm = compose_config([BACKEND], probe, cwd=root,
                             profiles=["rag", "pipeline", "gpu"])
    finally:
        os.remove(probe)
    svc_env = {k: (v.get("environment") or {}) for k, v in llm["services"].items()}
    users = {k: e for k, e in svc_env.items() if "VLLM_URL" in e}
    check("LLM_BASE_URL reaches every service that calls the model",
          bool(users) and all(e["VLLM_URL"] == "http://llm-probe:1234"
                              for e in users.values()),
          f"got {({k: e.get('VLLM_URL') for k, e in users.items()})}")
    check("LLM_MODEL_NAME reaches every service that names the model",
          all(e.get("VLLM_MODEL_NAME") == "probe-model" for e in users.values()))

    # --- 4. frontend paths survive being the SECOND file -------------------
    print("\nfrontend paths with backend first in COMPOSE_FILE")
    fe = [s for s in srcs if "/frontend/" in s or s.endswith("/frontend")]
    check("frontend sources resolve under frontend/", bool(fe),
          "none found; a literal ./path would land in backend/docker/")
    misrouted = [s for s in srcs
                 if s.startswith(os.path.join(root, "backend/docker"))
                 and "backend/docker" not in ("searxng", "grobid", "prometheus")
                 and not any(k in s for k in ("grobid", "prometheus", "searxng"))]
    check("nothing misrouted into backend/docker/", not misrouted,
          f"misrouted: {misrouted[:4]}")

    # --- frontend standalone, as the VPS runs it ---------------------------
    print("\nfrontend standalone (as the VPS runs it)")
    try:
        compose_config(["docker-compose.yml"], None, cwd=os.path.join(root, "frontend"))
        check("frontend refuses to start without MUNIN_DOMAIN", False,
              "it resolved with no domain set")
    except RuntimeError as e:
        check("frontend refuses to start without MUNIN_DOMAIN",
              "MUNIN_DOMAIN" in str(e), str(e)[:200])
    fe_other = compose_config(["docker-compose.yml"], envs["other"],
                              cwd=os.path.join(root, "frontend"), profiles=["webui"])
    check("no reference-deployment value in the frontend for another domain",
          "muninai" not in json.dumps(fe_other))
    fe_ref = compose_config(["docker-compose.yml"], envs["reference"],
                            cwd=os.path.join(root, "frontend"))
    auth_env = fe_ref["services"]["munin-auth"]["environment"]
    caddy_env = fe_ref["services"]["caddy"]["environment"]
    check("the reference domain resolves as production did",
          auth_env.get("COOKIE_DOMAIN") == ".muninai.org"
          and auth_env.get("SMTP_SENDER") == "noreply@muninai.org"
          and caddy_env.get("ACME_EMAIL") == "info@muninai.org"
          and caddy_env.get("MUNIN_URL_CHAT") == "https://chat.muninai.org",
          f"auth {auth_env.get('COOKIE_DOMAIN')!r}, caddy {caddy_env.get('ACME_EMAIL')!r}")
    cfg = fe_ref
    srcs = sources(cfg)
    fe_root = os.path.join(root, "frontend")
    ok = all(s.startswith((fe_root, os.path.join(root, "shared"), "/mnt/uploads"))
             for s in srcs)
    check("resolves relative to frontend/", ok,
          f"unexpected: {[s for s in srcs if not s.startswith(fe_root)][:4]}")
    caddyfile = next((c for svc in cfg["services"].values()
                      for c in (svc.get("command") or [])
                      if isinstance(c, str) and "Caddyfile" in c), None)
    check("defaults to the production Caddyfile",
          caddyfile == "/etc/caddy/Caddyfile", f"got {caddyfile!r}")

    # --- 7. scripts/configure.sh, every mode, from a fresh copy -------------
    print("\ninstall modes (scripts/configure.sh in a fresh copy of the tree)")
    check_modes(root)

    print(f"\n{checks - len(failures)}/{checks} checks passed")
    if failures:
        print("FAILED: " + ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
