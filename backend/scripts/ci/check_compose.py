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

    print(f"\n{checks - len(failures)}/{checks} checks passed")
    if failures:
        print("FAILED: " + ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
