# ==============================================================================
# MUNIN SANDBOX SERVICE - Dockerfile
# ==============================================================================
# A small FastAPI service that wraps Jupyter kernels and exposes them as a
# REST API to the retrieval container. Each kernel runs the user's Python
# code in isolation (app/isolation.py): `unshare --net` for a loopback-only
# network namespace, a uid leased to that conversation alone with no
# capabilities, hard rlimits, no host filesystem mounts. The retrieval service calls
# /exec/{conversation_id} via the internal docker network only - the host
# never reaches this container directly.
# ==============================================================================

FROM python:3.11-slim

WORKDIR /app

# TeX Live install (§18 LaTeX via sandbox). Split into its own layer so
# other Dockerfile edits don't invalidate the ~1 GB texlive cache on
# every rebuild. Medium-set package list chosen per the §18 spec:
# latex-base + latex-extra + latex-recommended covers the common class
# and package set; science adds math-heavy packages (amsmath extras,
# physics, siunitx); bibtex-extra pulls in the bibtex binary + common
# .bst styles; fonts-recommended gets the usual CM+Latin Modern fonts
# plus a handful of widely-used PS fonts. lang-german + lang-european
# ship the babel .ldf language definitions (ngerman.ldf, frenchb.ldf, ...)
# that base TeX Live no longer carries; without them
# \usepackage[ngerman]{babel} fails with "Unknown option 'ngerman'", which
# stranded a German user's compile on 2026-06-16 (chat 5e27dfa4).
# ~1 GB in the final image.
RUN apt-get update && apt-get install -y --no-install-recommends \
        texlive-latex-base \
        texlive-latex-extra \
        texlive-latex-recommended \
        texlive-science \
        texlive-bibtex-extra \
        texlive-fonts-recommended \
        texlive-lang-german \
        texlive-lang-european \
        biber \
    && rm -rf /var/lib/apt/lists/*

# System dependencies: build tools for the few wheels that compile from
# source on slim, plus tini for proper child-process reaping (matplotlib
# subprocesses, dead kernels, etc.). No firejail: inside a container it
# detects "an existing sandbox" and runs commands with no sandboxing at all,
# which is what the sandbox relied on until 2026-09-30. `unshare` ships in
# util-linux, already in the base image.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tini \
        build-essential \
        gfortran \
        libopenblas-dev \
    && rm -rf /var/lib/apt/lists/*

# Install the pre-baked package set the kernels will see.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Drop build deps once the wheels are installed - shaves ~300 MB from the
# final image and removes a meaningful chunk of attack surface inside the
# sandbox container.
RUN apt-get purge -y --auto-remove build-essential gfortran libopenblas-dev \
    && rm -rf /var/lib/apt/lists/*

# Application code.
COPY app /app/app/

# Scratch root the kernels work under. Container overlay only: files survive
# restart but die on `docker rm`. Matches the throwaway-by-design intent.
# 0711: kernels can enter their own 0700 conversation dir but not list the
# others. Manifests and per-uid runtime dirs are root-owned and closed to
# kernels; isolation.ensure_roots() re-asserts all three at startup.
RUN mkdir -p /scratch /scratch-meta /run/munin-sandbox \
    && chmod 0711 /scratch /run/munin-sandbox && chmod 0700 /scratch-meta

# Configure matplotlib to never need a display. The kernel still emits PNGs
# via display_data; this just stops it from trying to spin up Tk.
#
# Cap numerical-library thread pools at 1 (chat 56b39f33, 2026-06-03).
# OpenBLAS, OpenMP, MKL, and numexpr all detect the host's core count and
# eagerly spin up that many worker threads on first numpy/scipy use. The
# isolated kernel runs under RLIMIT_NPROC; on a multi-core host this means
# `pthread_create failed for thread 11 of 24: Resource temporarily
# unavailable` cascades during numpy's BLAS init, which also breaks any
# downstream import that touches numpy (e.g. matplotlib, sklearn). The
# sandbox is for interactive correctness experiments, not number crunching;
# single-thread BLAS is the right tradeoff and removes a major failure mode
# the model previously misdiagnosed as "the sandbox is struggling".
ENV MPLBACKEND=Agg \
    PYTHONUNBUFFERED=1 \
    SANDBOX_SCRATCH_DIR=/scratch \
    SANDBOX_PORT=8090 \
    OPENBLAS_NUM_THREADS=1 \
    OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1

EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8090/healthz').raise_for_status()"

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8090"]
