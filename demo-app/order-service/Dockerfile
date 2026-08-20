FROM python:3.12-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --root-user-action=ignore --target=/deps -r requirements.txt

FROM python:3.12-slim AS runtime
RUN groupadd --gid 1001 appuser && useradd --uid 1001 --gid 1001 --no-log-init --no-create-home appuser
WORKDIR /app
COPY --from=builder /deps /usr/local/lib/python3.12/site-packages/
COPY app/ ./app/
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8002/health')" || exit 1
USER appuser
EXPOSE 8002
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8002", "--workers", "1"]
