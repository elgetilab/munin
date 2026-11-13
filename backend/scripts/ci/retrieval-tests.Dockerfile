# ==============================================================================
# Hermetic test image for backend/retrieval/tests (GitHub CI)
# ==============================================================================
# The production image (backend/retrieval/Dockerfile) is ~10 GB, almost all of
# it torch + CUDA wheels for the encoders. The hermetic tier never loads an
# encoder: every test that needs one either stubs it or SKIPs when it is
# absent. So this image is the production one minus torch and
# sentence-transformers, plus pytest for the pytest-native files, and builds
# in a couple of minutes on a stock runner.
#
# It carries NO source. The repo is mounted at run time (read-only) and the
# suite runs with --network none:
#
#   docker build -t munin-retrieval-tests -f backend/scripts/ci/retrieval-tests.Dockerfile backend/retrieval
#   backend/scripts/ci/run_retrieval_tests.sh
#
# Same base and same constraints.txt as production, so a dependency bump that
# breaks a test breaks it here too. The one deliberate difference is the
# primer-design oracle tooling (primer3, BLAST+, Biopython) added below, which
# production must NOT have; the license note there says why.
# ==============================================================================

FROM python:3.11.14-slim

# OCR for test_pdf_ocr, same packages the production image installs.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ocrmypdf \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

# ------------------------------------------------------------------------------
# Differential oracles for primer/ (test image ONLY, see the license note)
# ------------------------------------------------------------------------------
# primer3 gives us primer3_core, ntthal and oligotm; ncbi-blast+ gives us
# blastn and makeblastdb for the specificity work. Both are baked in because
# the hermetic tier runs with --network none, so nothing can be fetched at
# test time. They back tests/test_primer_thermo_oracle.py, which checks our
# from-scratch thermodynamics against independent implementations
# (docs/agent-track/PRIMER-DESIGN-PLAN.md, "Layer 1b").
#
# *** LICENSE, READ BEFORE MOVING THIS ANYWHERE ***
#
# Primer3 is GPL-2.0-or-later. Munin is Apache-2.0. These binaries may live
# HERE and ONLY here: this image is built in CI, used, and thrown away; it is
# never published, pushed to a registry or distributed, and GPL obligations
# attach to distribution. Do NOT add primer3 to backend/retrieval/Dockerfile,
# to backend/retrieval/requirements.txt, or to any image that ships.
#
# What we do is what Primer3's own web help, section 4 "FAIR USE OF PRIMER3",
# blesses verbatim: "If you only call Primer3 from your software and interpret
# its output, you can use any license you want for your software." We exec the
# binary and parse stdout. We do not link it, vendor its source, or copy its
# GPL test fixtures (their expected values are generated here, by running the
# binary, not transcribed).
#
# ncbi-blast+ carries no such constraint (NCBI BLAST+ is US-government public
# domain), and neither does Biopython (Biopython License, BSD-3-like); they
# are here rather than in production only because production has no use for
# them. Biopython is pip-installed below instead of via python3-biopython,
# which targets Debian's python3.13 and would be invisible to this image's
# python3.11.
#
# SIZE: this layer is 171 MB, and essentially all of it is BLAST+: primer3 is
# 0.8 MB, ncbi-blast+ plus ncbi-data is 101 MB, and BLAST+ depends on perl,
# which is another 50 MB. Biopython adds 22 MB on the pip side. If that ever
# becomes a problem, BLAST+ is the piece to split into its own image or stage:
# drop `ncbi-blast+` from this line and test_primer_thermo_oracle.py's BLAST
# test SKIPs cleanly instead of failing.
RUN apt-get update && apt-get install -y --no-install-recommends \
    primer3 \
    ncbi-blast+ \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt constraints.txt /tmp/req/
RUN grep -vE '^(torch|sentence-transformers)\b' /tmp/req/requirements.txt > /tmp/req/requirements-ci.txt \
    && pip install --no-cache-dir -r /tmp/req/requirements-ci.txt -c /tmp/req/constraints.txt \
    && pip install --no-cache-dir 'pytest>=8' 'biopython==1.88' -c /tmp/req/constraints.txt \
    && rm -rf /tmp/req

ENV PYTHONDONTWRITEBYTECODE=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1
