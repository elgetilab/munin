# ==============================================================================
# MUNIN KNOWLEDGE SERVICE - Dockerfile
# ==============================================================================
# Runs the nightly paper-embedding map: scroll Qdrant, UMAP to 10d, HDBSCAN,
# UMAP to 2d for coordinates, label each cluster via the LLM, write back
# cluster_id/topic_label/topic_slug and emit embedding_map.json.
#
# This is a SEPARATE image on purpose. The pipeline jobs (watcher, detect,
# reattribute) reuse the retrieval image because every dependency they import
# is already in it. This one cannot: it needs umap-learn and hdbscan, which
# retrieval does not have. Building it FROM retrieval would inherit torch for a
# job that never touches a model, so it starts from python:3.11-slim instead.
#
# Measured 2026-08-14: this image is 941 MB against retrieval's 8.67 GB. Not
# small in absolute terms -- numba, llvmlite and scikit-learn come along with
# UMAP and HDBSCAN -- but roughly a tenth of the alternative.
#
# The script itself is NOT copied in: it is mounted at /app/scripts from
# MUNIN_SCRIPTS_SRC, the same way retrieval already mounts the paper pipeline.
# That keeps an edit-and-restart loop working without a rebuild.
# ==============================================================================

FROM python:3.11-slim AS builder

WORKDIR /build

# hdbscan and umap-learn compile against numpy/Cython at install time, so the
# build stage needs a toolchain. The final stage does not.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# ==============================================================================
FROM python:3.11-slim

WORKDIR /app

COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

# `date -d` in run-daily.sh is GNU coreutils, present in the slim image.
# No other runtime packages are needed.

# Numba (via UMAP) JIT-compiles on first use and wants a writable cache. Point
# it somewhere writable by default; the compose service overrides it onto the
# persistent knowledge volume so the compile cost is paid once, not per run.
ENV NUMBA_CACHE_DIR=/tmp/numba-cache

# No CMD: the compose service supplies the schedule and the command, so the
# same image can also be used for a one-off manual rebuild via `docker run`.
