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
# breaks a test breaks it here too.
# ==============================================================================

FROM python:3.11.14-slim

# OCR for test_pdf_ocr, same packages the production image installs.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ocrmypdf \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt constraints.txt /tmp/req/
RUN grep -vE '^(torch|sentence-transformers)\b' /tmp/req/requirements.txt > /tmp/req/requirements-ci.txt \
    && pip install --no-cache-dir -r /tmp/req/requirements-ci.txt -c /tmp/req/constraints.txt \
    && pip install --no-cache-dir 'pytest>=8' -c /tmp/req/constraints.txt \
    && rm -rf /tmp/req

ENV PYTHONDONTWRITEBYTECODE=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1
