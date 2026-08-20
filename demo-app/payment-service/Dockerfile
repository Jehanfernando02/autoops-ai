FROM python:3.12-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN python -m venv /venv && \
    /venv/bin/pip install --no-cache-dir --upgrade pip && \
    /venv/bin/pip install --no-cache-dir -r requirements.txt

FROM python:3.12-slim AS runtime
RUN groupadd --gid 1001 appuser && \
    useradd --uid 1001 --gid 1001 --no-log-init --no-create-home appuser
WORKDIR /app
COPY --from=builder /venv /venv
COPY app/ ./app/
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD /venv/bin/python -c "import urllib.request; urllib.request.urlopen('http://localhost:8003/health')" || exit 1
USER appuser
EXPOSE 8003
CMD ["/venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8003", "--workers", "1"]
