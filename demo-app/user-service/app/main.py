import json
import logging
import os
import random
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel, EmailStr, Field

# -----------------------------------------------------------
# Structured JSON logging - Loki picks these up and lets
# us filter by service, level, user_id, etc.
# -----------------------------------------------------------
class JSONFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "service": "user-service",
            "message": record.getMessage(),
        })

handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
logging.basicConfig(level=logging.INFO, handlers=[handler])
logger = logging.getLogger("user-service")

# -----------------------------------------------------------
# Prometheus metrics
# We track: how many requests came in, how fast they were,
# how many users are currently registered (gauge).
# -----------------------------------------------------------
REQUEST_COUNT = Counter(
    "user_requests_total", "Total requests to user-service",
    ["method", "endpoint", "status_code"],
)
REQUEST_LATENCY = Histogram(
    "user_request_duration_seconds", "Request latency",
    ["endpoint"],
)
REGISTERED_USERS = Gauge(
    "user_registered_total", "Total registered users in memory",
)
LOGIN_ATTEMPTS = Counter(
    "user_login_attempts_total", "Login attempts",
    ["result"],  # success / failure
)

# -----------------------------------------------------------
# Chaos configuration - inject failures via env vars.
# In Kubernetes we patch the ConfigMap to change these.
# -----------------------------------------------------------
CHAOS_ERROR_RATE = float(os.getenv("CHAOS_ERROR_RATE", "0.0"))
CHAOS_LATENCY_MS = int(os.getenv("CHAOS_LATENCY_MS", "0"))

# Simple in-memory user store (no real DB for Phase 1)
_users: dict[str, dict] = {}
_start_time = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("user-service starting up")
    # seed some fake users so the demo looks alive
    for i in range(5):
        uid = str(uuid.uuid4())
        _users[uid] = {
            "id": uid,
            "name": f"Demo User {i+1}",
            "email": f"user{i+1}@demo.local",
            "created_at": datetime.utcnow().isoformat() + "Z",
        }
    REGISTERED_USERS.set(len(_users))
    logger.info(f"Seeded {len(_users)} demo users")
    yield
    logger.info("user-service shutting down")


app = FastAPI(
    title="User Service",
    description="User management for AutoOps demo app",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# -----------------------------------------------------------
# Pydantic models - FastAPI validates all request/response
# bodies automatically against these schemas.
# -----------------------------------------------------------
class UserCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    email: str = Field(..., description="User email")

class UserResponse(BaseModel):
    id: str
    name: str
    email: str
    created_at: str


# -----------------------------------------------------------
# Middleware - wraps every request to record metrics
# -----------------------------------------------------------
@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    import asyncio
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


# -----------------------------------------------------------
# Health probes
# /health → liveness  (is the process alive?)
# /ready  → readiness (is it safe to send traffic?)
# -----------------------------------------------------------
@app.get("/health", tags=["Health"])
async def health():
    return {
        "status": "healthy",
        "service": "user-service",
        "uptime_seconds": round(time.time() - _start_time, 2),
    }

@app.get("/ready", tags=["Health"])
async def ready():
    return {"status": "ready"}

@app.get("/metrics", tags=["Observability"])
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# -----------------------------------------------------------
# User endpoints
# -----------------------------------------------------------
@app.post("/users", response_model=UserResponse, status_code=201, tags=["Users"])
async def create_user(body: UserCreate):
    """Create a new user."""
    if CHAOS_ERROR_RATE > 0 and random.random() < CHAOS_ERROR_RATE:
        logger.error("Failed to create user: chaos error injected")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    # Check for duplicate email
    if any(u["email"] == body.email for u in _users.values()):
        raise HTTPException(status_code=409, detail="Email already registered")

    uid = str(uuid.uuid4())
    user = {
        "id": uid,
        "name": body.name,
        "email": body.email,
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    _users[uid] = user
    REGISTERED_USERS.set(len(_users))
    logger.info(f"Created user {uid} ({body.email})")
    return UserResponse(**user)


@app.get("/users", tags=["Users"])
async def list_users(limit: int = 20, offset: int = 0):
    """List all users (paginated)."""
    all_users = list(_users.values())
    return {
        "users": all_users[offset: offset + limit],
        "total": len(all_users),
        "limit": limit,
        "offset": offset,
    }


@app.get("/users/{user_id}", response_model=UserResponse, tags=["Users"])
async def get_user(user_id: str):
    """Get a single user by ID."""
    user = _users.get(user_id)
    if not user:
        logger.warning(f"User {user_id} not found")
        raise HTTPException(status_code=404, detail="User not found")
    return UserResponse(**user)


@app.delete("/users/{user_id}", status_code=204, tags=["Users"])
async def delete_user(user_id: str):
    """Delete a user."""
    if user_id not in _users:
        raise HTTPException(status_code=404, detail="User not found")
    del _users[user_id]
    REGISTERED_USERS.set(len(_users))
    logger.info(f"Deleted user {user_id}")
