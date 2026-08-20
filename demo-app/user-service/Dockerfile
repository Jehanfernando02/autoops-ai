# ─────────────────────────────────────────────────────────────
# user-service Dockerfile
#
# MULTI-STAGE BUILD: Uses two FROM stages.
#   Stage 1 (builder): installs all dependencies into a venv
#   Stage 2 (runtime): copies only the venv — no build tools
#
# WHY VENV instead of --target?
# pip --target copies library files but NOT scripts (uvicorn, etc)
# A virtualenv correctly bundles both libraries AND executables.
#
# WHY NON-ROOT?
# Running as UID 1001 limits blast radius if the container is compromised.
# ─────────────────────────────────────────────────────────────

# ── Stage 1: Builder ─────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Layer caching: copy requirements first. If it hasn't changed,
# Docker reuses the cached pip install layer on next build.
COPY requirements.txt .

# Create a virtualenv and install all packages into it.
# The venv lives at /venv — we'll copy this entire folder to Stage 2.
RUN python -m venv /venv && \
    /venv/bin/pip install --no-cache-dir --upgrade pip && \
    /venv/bin/pip install --no-cache-dir -r requirements.txt


# ── Stage 2: Runtime ─────────────────────────────────────────
FROM python:3.12-slim AS runtime

RUN groupadd --gid 1001 appuser && \
    useradd --uid 1001 --gid 1001 --no-log-init --no-create-home appuser

WORKDIR /app

# Copy the entire virtualenv from builder (includes uvicorn binary)
COPY --from=builder /venv /venv

# Copy application code
COPY app/ ./app/

# Note: Kubernetes ignores HEALTHCHECK — it uses liveness/readiness
# probes defined in the Deployment YAML instead.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD /venv/bin/python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/health')" || exit 1

USER appuser
EXPOSE 8001

# Use the venv's uvicorn directly (not system PATH)
CMD ["/venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1"]
