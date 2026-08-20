# ─────────────────────────────────────────────────────────────
# user-service Dockerfile
#
# WHAT IS A MULTI-STAGE BUILD?
# Docker builds images layer by layer. Every RUN, COPY, ADD
# creates a new layer. A "multi-stage" build uses multiple FROM
# statements — the final image only contains what you explicitly
# copy from previous stages. This means build tools, compilers,
# and pip caches don't end up in your production image.
#
# WHY NON-ROOT?
# If your container runs as root and an attacker exploits it,
# they have root on the host too (in misconfigured setups).
# Running as UID 1000 limits the blast radius.
# ─────────────────────────────────────────────────────────────

# ── Stage 1: Builder ─────────────────────────────────────────
# We use the full python:3.12 image here because we need pip
# and potentially compilers for packages with C extensions.
FROM python:3.12-slim AS builder

WORKDIR /build

# Copy requirements first — Docker caches this layer.
# If requirements.txt hasn't changed, this whole step is skipped
# on the next build. This makes rebuilds much faster.
COPY requirements.txt .

# --no-cache-dir : don't store pip's download cache (saves ~50MB)
# --target /deps : install packages into /deps instead of site-packages
#                  so we can easily copy them in Stage 2
RUN pip install --no-cache-dir --target=/deps -r requirements.txt


# ── Stage 2: Runtime ─────────────────────────────────────────
# python:3.12-slim is ~50MB vs ~350MB for the full image.
# It has Python but none of the build tools.
FROM python:3.12-slim AS runtime

# Create a non-root user and group
# -r = system user (no home dir, no shell)
# -g appuser = assign to appuser group
# -u 1000 = explicit UID (good practice for K8s security contexts)
RUN groupadd -r appuser && useradd -r -g appuser -u 1000 appuser

WORKDIR /app

# Copy installed packages from builder stage
COPY --from=builder /deps /usr/local/lib/python3.12/site-packages/

# Copy just the application code
COPY app/ ./app/

# HEALTHCHECK tells Docker how to test if the container is healthy.
# Note: Kubernetes IGNORES this and uses its own liveness/readiness
# probes defined in the Deployment YAML. But this is useful for
# local docker compose and docker inspect.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/health')" || exit 1

# Switch to non-root user before running the app
USER appuser

EXPOSE 8001

# uvicorn: the ASGI server that runs FastAPI
# --host 0.0.0.0 : listen on all interfaces (not just localhost)
# --port 8001    : match the EXPOSE above
# --workers 1    : single worker (K8s handles scaling via replicas)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1"]
