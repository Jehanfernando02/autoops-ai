import asyncio
import json
import logging
import os
import random
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from enum import Enum

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel, Field

# -----------------------------------------------------------
# Structured JSON logging for Loki
# -----------------------------------------------------------
class JSONFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "service": "order-service",
            "message": record.getMessage(),
        })

handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
logging.basicConfig(level=logging.INFO, handlers=[handler])
logger = logging.getLogger("order-service")

# -----------------------------------------------------------
# Prometheus metrics
# -----------------------------------------------------------
REQUEST_COUNT = Counter(
    "order_requests_total", "Total requests",
    ["method", "endpoint", "status_code"],
)
REQUEST_LATENCY = Histogram(
    "order_request_duration_seconds", "Request latency",
    ["endpoint"],
)
ORDERS_CREATED = Counter(
    "orders_created_total", "Orders created",
    ["status"],
)
ORDER_VALUE = Histogram(
    "order_value_dollars", "Order values",
    buckets=[5, 10, 25, 50, 100, 250, 500],
)

# -----------------------------------------------------------
# Config
# USER_SERVICE_URL: where to reach user-service inside K8s
# In Kubernetes, services talk to each other using their
# Service name: http://<service-name>.<namespace>.svc.cluster.local
# -----------------------------------------------------------
USER_SERVICE_URL = os.getenv("USER_SERVICE_URL", "http://user-service:8001")
CHAOS_ERROR_RATE = float(os.getenv("CHAOS_ERROR_RATE", "0.0"))
CHAOS_LATENCY_MS = int(os.getenv("CHAOS_LATENCY_MS", "0"))

# In-memory store for Phase 1
_orders: dict[str, dict] = {}
_start_time = time.time()


class OrderStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    PROCESSING = "processing"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("order-service starting up")
    logger.info(f"User service URL: {USER_SERVICE_URL}")
    yield
    logger.info("order-service shutting down")


app = FastAPI(
    title="Order Service",
    description="Order management for AutoOps demo app",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class OrderItem(BaseModel):
    product_id: str
    product_name: str
    quantity: int = Field(..., gt=0)
    unit_price: float = Field(..., gt=0)

class OrderCreate(BaseModel):
    user_id: str
    items: list[OrderItem]

class OrderResponse(BaseModel):
    id: str
    user_id: str
    items: list[dict]
    total: float
    status: str
    created_at: str


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    if CHAOS_LATENCY_MS > 0:
        await asyncio.sleep(CHAOS_LATENCY_MS / 1000)
    start = time.time()
    response = await call_next(request)
    duration = time.time() - start
    if request.url.path not in ["/metrics", "/health", "/ready"]:
        REQUEST_COUNT.labels(
            method=request.method,
            endpoint=request.url.path,
            status_code=response.status_code,
        ).inc()
        REQUEST_LATENCY.labels(endpoint=request.url.path).observe(duration)
    return response


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "healthy", "service": "order-service", "uptime_seconds": round(time.time() - _start_time, 2)}

@app.get("/ready", tags=["Health"])
async def ready():
    return {"status": "ready"}

@app.get("/metrics", tags=["Observability"])
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/orders", response_model=OrderResponse, status_code=201, tags=["Orders"])
async def create_order(body: OrderCreate):
    """
    Create a new order.
    Calls user-service first to verify the user exists.
    This is what service-to-service communication looks like in microservices.
    """
    if CHAOS_ERROR_RATE > 0 and random.random() < CHAOS_ERROR_RATE:
        logger.error(f"Order creation failed for user {body.user_id}: chaos error injected")
        ORDERS_CREATED.labels(status="failed").inc()
        raise HTTPException(status_code=503, detail="Order service temporarily unavailable")

    # -----------------------------------------------------------
    # Service-to-service call: verify user exists
    # In K8s: http://user-service.demo-app.svc.cluster.local:8001
    # httpx is the async HTTP client (like requests but async)
    # -----------------------------------------------------------
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{USER_SERVICE_URL}/users/{body.user_id}")
            if resp.status_code == 404:
                raise HTTPException(status_code=404, detail="User not found")
            resp.raise_for_status()
    except httpx.TimeoutException:
        logger.error(f"Timeout calling user-service for user {body.user_id}")
        raise HTTPException(status_code=504, detail="User service timeout")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error calling user-service: {e}")
        raise HTTPException(status_code=502, detail="Could not verify user")

    total = sum(item.quantity * item.unit_price for item in body.items)
    order_id = str(uuid.uuid4())
    order = {
        "id": order_id,
        "user_id": body.user_id,
        "items": [item.model_dump() for item in body.items],
        "total": round(total, 2),
        "status": OrderStatus.PENDING,
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    _orders[order_id] = order
    ORDERS_CREATED.labels(status="success").inc()
    ORDER_VALUE.observe(total)
    logger.info(f"Order {order_id} created for user {body.user_id}, total=${total:.2f}")
    return OrderResponse(**order)


@app.get("/orders/{order_id}", response_model=OrderResponse, tags=["Orders"])
async def get_order(order_id: str):
    order = _orders.get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return OrderResponse(**order)


@app.get("/orders", tags=["Orders"])
async def list_orders(user_id: str | None = None, limit: int = 20):
    all_orders = list(_orders.values())
    if user_id:
        all_orders = [o for o in all_orders if o["user_id"] == user_id]
    return {"orders": all_orders[:limit], "total": len(all_orders)}


@app.patch("/orders/{order_id}/status", tags=["Orders"])
async def update_order_status(order_id: str, status: OrderStatus):
    order = _orders.get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    order["status"] = status
    logger.info(f"Order {order_id} status updated to {status}")
    return {"id": order_id, "status": status}
