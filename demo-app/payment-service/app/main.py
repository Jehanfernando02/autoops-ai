# ──────────────────────────────────────────────────────────────────
# payment-service/app/main.py
#
# LEARNING: This is a FastAPI microservice simulating payment processing.
# Key concepts demonstrated:
#   1. FastAPI app structure with lifespan (startup/shutdown hooks)
#   2. Prometheus metrics instrumentation (counter, histogram, gauge)
#   3. Structured JSON logging (Loki-friendly)
#   4. Health/readiness endpoints (required by Kubernetes probes)
#   5. Intentional failure modes (injectable via env vars for chaos demos)
# ──────────────────────────────────────────────────────────────────

import asyncio
import json
import logging
import os
import random
import time
from contextlib import asynccontextmanager
from datetime import datetime

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel, Field

# ──────────────────────────────────────────────────────────────────
# STRUCTURED LOGGING
# Loki works best with JSON logs — each log line is a JSON object.
# This makes it easy to filter logs by fields like service, level, etc.
# ──────────────────────────────────────────────────────────────────
class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "service": "payment-service",
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry)

handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
logging.basicConfig(level=logging.INFO, handlers=[handler])
logger = logging.getLogger("payment-service")

# ──────────────────────────────────────────────────────────────────
# PROMETHEUS METRICS
# These are the metrics Prometheus will scrape from the /metrics endpoint.
# Counter: only goes up (e.g. request count, error count)
# Histogram: measures distributions (e.g. request latency in buckets)
# Gauge: can go up or down (e.g. active connections, memory usage)
# ──────────────────────────────────────────────────────────────────
REQUEST_COUNT = Counter(
    "payment_requests_total",
    "Total payment requests",
    ["method", "endpoint", "status_code"],
)
REQUEST_LATENCY = Histogram(
    "payment_request_duration_seconds",
    "Payment request latency in seconds",
    ["endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)
PAYMENT_PROCESSED = Counter(
    "payments_processed_total",
    "Total payments processed",
    ["status"],  # success or failure
)
PAYMENT_AMOUNT = Histogram(
    "payment_amount_dollars",
    "Payment amounts in dollars",
    buckets=[1, 5, 10, 25, 50, 100, 250, 500, 1000],
)
ACTIVE_CONNECTIONS = Gauge(
    "payment_active_connections",
    "Number of active payment connections",
)
MEMORY_USAGE_BYTES = Gauge(
    "payment_memory_bytes",
    "Simulated memory usage in bytes",
)

# ──────────────────────────────────────────────────────────────────
# CHAOS CONFIGURATION
# Read from environment variables. This is how we inject failures
# without changing code. In Kubernetes, we update the ConfigMap
# and the pod picks up the new values on restart (or via a watcher).
# ──────────────────────────────────────────────────────────────────
CHAOS_MEMORY_LEAK = os.getenv("CHAOS_MEMORY_LEAK", "false").lower() == "true"
CHAOS_CPU_SPIKE = os.getenv("CHAOS_CPU_SPIKE", "false").lower() == "true"
CHAOS_ERROR_RATE = float(os.getenv("CHAOS_ERROR_RATE", "0.0"))  # 0.0 to 1.0
CHAOS_LATENCY_MS = int(os.getenv("CHAOS_LATENCY_MS", "0"))       # extra ms delay

# This simulates a memory leak — we accumulate data that's never freed.
_memory_leak_store: list = []

# ──────────────────────────────────────────────────────────────────
# APP LIFESPAN
# Modern FastAPI uses lifespan instead of @app.on_event("startup").
# Code before `yield` runs on startup; code after runs on shutdown.
# ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("payment-service starting up")

    # Start background tasks
    if CHAOS_MEMORY_LEAK:
        logger.warning("⚠️  CHAOS MODE: Memory leak enabled")
        asyncio.create_task(simulate_memory_leak())

    if CHAOS_CPU_SPIKE:
        logger.warning("⚠️  CHAOS MODE: CPU spike enabled")
        asyncio.create_task(simulate_cpu_spike())

    asyncio.create_task(update_metrics_periodically())

    yield  # App runs here

    logger.info("payment-service shutting down")


app = FastAPI(
    title="Payment Service",
    description="Handles payment processing for the AutoOps demo application",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ──────────────────────────────────────────────────────────────────
# PYDANTIC MODELS
# Pydantic validates request/response bodies automatically.
# FastAPI uses these to generate OpenAPI docs too.
# ──────────────────────────────────────────────────────────────────
class PaymentRequest(BaseModel):
    order_id: str = Field(..., description="Order to pay for")
    amount: float = Field(..., gt=0, description="Amount in dollars")
    currency: str = Field(default="USD")
    payment_method: str = Field(default="card")

class PaymentResponse(BaseModel):
    payment_id: str
    order_id: str
    status: str
    amount: float
    currency: str
    processed_at: str

class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    uptime_seconds: float
    chaos_modes: dict

_start_time = time.time()

# ──────────────────────────────────────────────────────────────────
# MIDDLEWARE
# Middleware wraps every request. We use it to:
# 1. Record request count (with labels: method, endpoint, status)
# 2. Measure latency
# 3. Track active connections
# ──────────────────────────────────────────────────────────────────
@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    ACTIVE_CONNECTIONS.inc()
    start = time.time()
    response = await call_next(request)
    duration = time.time() - start

    # Don't track /metrics or /health in request metrics (would pollute data)
    if request.url.path not in ["/metrics", "/health", "/ready"]:
        REQUEST_COUNT.labels(
            method=request.method,
            endpoint=request.url.path,
            status_code=response.status_code,
        ).inc()
        REQUEST_LATENCY.labels(endpoint=request.url.path).observe(duration)

    ACTIVE_CONNECTIONS.dec()
    return response

# ──────────────────────────────────────────────────────────────────
# HEALTH ENDPOINTS
# Kubernetes uses two probe types:
#   - Liveness probe: Is the process alive? Fail = restart the pod.
#   - Readiness probe: Is it ready to serve traffic? Fail = remove from LB.
# They should be separate! A pod can be alive but not ready.
# ──────────────────────────────────────────────────────────────────
@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check():
    """Liveness probe — checks if the service is running."""
    return HealthResponse(
        status="healthy",
        service="payment-service",
        version="1.0.0",
        uptime_seconds=time.time() - _start_time,
        chaos_modes={
            "memory_leak": CHAOS_MEMORY_LEAK,
            "cpu_spike": CHAOS_CPU_SPIKE,
            "error_rate": CHAOS_ERROR_RATE,
            "latency_ms": CHAOS_LATENCY_MS,
        },
    )

@app.get("/ready", tags=["Health"])
async def readiness_check():
    """Readiness probe — checks if the service can serve traffic."""
    # In a real service, you'd also check DB connectivity here.
    return {"status": "ready", "service": "payment-service"}

# ──────────────────────────────────────────────────────────────────
# PROMETHEUS METRICS ENDPOINT
# Prometheus scrapes this endpoint every ~15 seconds.
# It reads all registered metrics and stores them in its time-series DB.
# ──────────────────────────────────────────────────────────────────
@app.get("/metrics", tags=["Observability"])
async def metrics():
    """Prometheus metrics scrape endpoint."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

# ──────────────────────────────────────────────────────────────────
# PAYMENT ENDPOINTS
# ──────────────────────────────────────────────────────────────────
@app.post("/payments", response_model=PaymentResponse, tags=["Payments"])
async def process_payment(payment: PaymentRequest, request: Request):
    """Process a payment. May fail based on chaos configuration."""

    logger.info(f"Processing payment for order {payment.order_id}, amount ${payment.amount}")

    # ── Chaos: inject artificial latency ──────────────────────────
    if CHAOS_LATENCY_MS > 0:
        await asyncio.sleep(CHAOS_LATENCY_MS / 1000)

    # ── Chaos: inject random errors ───────────────────────────────
    if CHAOS_ERROR_RATE > 0 and random.random() < CHAOS_ERROR_RATE:
        logger.error(f"Payment failed for order {payment.order_id}: service degraded (chaos)")
        PAYMENT_PROCESSED.labels(status="failure").inc()
        raise HTTPException(status_code=503, detail="Payment service temporarily unavailable")

    # ── Simulate processing time ───────────────────────────────────
    processing_time = random.uniform(0.05, 0.3)
    await asyncio.sleep(processing_time)

    # ── Record metrics ─────────────────────────────────────────────
    PAYMENT_PROCESSED.labels(status="success").inc()
    PAYMENT_AMOUNT.observe(payment.amount)

    payment_id = f"pay_{int(time.time() * 1000)}_{random.randint(1000, 9999)}"

    logger.info(f"Payment {payment_id} processed successfully for order {payment.order_id}")

    return PaymentResponse(
        payment_id=payment_id,
        order_id=payment.order_id,
        status="success",
        amount=payment.amount,
        currency=payment.currency,
        processed_at=datetime.utcnow().isoformat() + "Z",
    )

@app.get("/payments/{payment_id}", tags=["Payments"])
async def get_payment(payment_id: str):
    """Get payment status by ID."""
    # In real service: query DB. Here we fake it.
    if not payment_id.startswith("pay_"):
        raise HTTPException(status_code=404, detail="Payment not found")
    return {
        "payment_id": payment_id,
        "status": "success",
        "retrieved_at": datetime.utcnow().isoformat() + "Z",
    }

@app.get("/payments", tags=["Payments"])
async def list_payments(limit: int = 10, offset: int = 0):
    """List recent payments (paginated)."""
    # Fake data for demo purposes
    return {
        "payments": [],
        "total": 0,
        "limit": limit,
        "offset": offset,
    }

# ──────────────────────────────────────────────────────────────────
# CHAOS SIMULATION BACKGROUND TASKS
# These run as asyncio tasks when chaos modes are enabled.
# In a real chaos scenario, you'd trigger these via env var + pod restart.
# ──────────────────────────────────────────────────────────────────
async def simulate_memory_leak():
    """Simulates a memory leak by accumulating data that's never freed.
    
    This will eventually cause the pod to be OOMKilled by Kubernetes.
    OOMKill (Out Of Memory Kill) is when the kernel kills a process
    because it exceeded its memory limit set in the K8s resource spec.
    """
    chunk_size_mb = 10
    logger.warning(f"Memory leak simulation started — adding {chunk_size_mb}MB every 5s")

    while True:
        # Allocate 10MB chunk and never release it
        chunk = " " * (chunk_size_mb * 1024 * 1024)
        _memory_leak_store.append(chunk)

        total_mb = len(_memory_leak_store) * chunk_size_mb
        MEMORY_USAGE_BYTES.set(total_mb * 1024 * 1024)

        logger.warning(f"Memory leaked: {total_mb}MB total allocated")
        await asyncio.sleep(5)


async def simulate_cpu_spike():
    """Simulates high CPU usage via a busy loop.
    
    This will trigger Prometheus CPU alerts and HPA (auto-scaler) to kick in.
    """
    logger.warning("CPU spike simulation started")
    while True:
        # Busy compute for 2 seconds, then rest for 0.5s
        end_time = time.time() + 2.0
        while time.time() < end_time:
            _ = sum(i * i for i in range(10000))
        await asyncio.sleep(0.5)


async def update_metrics_periodically():
    """Updates memory gauge periodically based on actual usage."""
    import resource
    while True:
        try:
            usage = resource.getrusage(resource.RUSAGE_SELF)
            MEMORY_USAGE_BYTES.set(usage.ru_maxrss)
        except Exception:
            pass
        await asyncio.sleep(15)
