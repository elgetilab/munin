# ==============================================================================
# MUNIN SANDBOX SERVICE - Dockerfile
# ==============================================================================
# A small FastAPI service that wraps Jupyter kernels and exposes them as a
# REST API to the retrieval container. Each kernel runs the user's Python
# code in isolation: firejail --net=none for network blocking, setrlimit for
# resource caps, no host filesystem mounts. The retrieval service calls
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
# plus a handful of widely-used PS fonts. ~1 GB in the final image.
RUN apt-get update && apt-get install -y --no-install-recommends \
        texlive-latex-base \
        texlive-latex-extra \
        texlive-latex-recommended \
        texlive-science \
        texlive-bibtex-extra \
        texlive-fonts-recommended \
        biber \
    && rm -rf /var/lib/apt/lists/*

# System dependencies: firejail for kernel network/fs sandboxing, build tools
# for the few wheels that compile from source on slim, plus tini for proper
# child-process reaping (matplotlib subprocesses, dead kernels, etc.).
RUN apt-get update && apt-get install -y --no-install-recommends \
        firejail \
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
COPY app/ /app/app/

# Writable scratch directory the kernels will use. Container overlay only:
# files survive restart but die on `docker rm`. Matches the throwaway-by-
# design intent.
RUN mkdir -p /scratch && chmod 1777 /scratch

# Configure matplotlib to never need a display. The kernel still emits PNGs
# via display_data; this just stops it from trying to spin up Tk.
ENV MPLBACKEND=Agg \
    PYTHONUNBUFFERED=1 \
    SANDBOX_SCRATCH_DIR=/scratch \
    SANDBOX_PORT=8090

EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8090/healthz').raise_for_status()"

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8090"]
