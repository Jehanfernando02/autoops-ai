import asyncio
import json
import logging
import os
import random
import time
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel

# -----------------------------------------------------------
# JSON logging for Loki
# -----------------------------------------------------------
class JSONFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "service": "worker-service",
            "message": record.getMessage(),
        })

handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
logging.basicConfig(level=logging.INFO, handlers=[handler])
logger = logging.getLogger("worker-service")

# -----------------------------------------------------------
# Prometheus metrics
# -----------------------------------------------------------
JOBS_PROCESSED = Counter(
    "worker_jobs_processed_total", "Jobs processed",
    ["status"],  # success / failure
)
JOB_DURATION = Histogram(
    "worker_job_duration_seconds", "Time to process a job",
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0],
)
QUEUE_DEPTH = Gauge(
    "worker_queue_depth", "Number of jobs waiting in queue",
)
WORKER_RESTARTS = Counter(
    "worker_crash_total", "Number of times worker crashed",
)

# -----------------------------------------------------------
# Chaos: CHAOS_CRASH_LOOP makes the worker crash every N jobs.
# This simulates a CrashLoopBackOff in Kubernetes — the pod
# keeps dying and K8s keeps restarting it.
# -----------------------------------------------------------
CHAOS_CRASH_EVERY_N = int(os.getenv("CHAOS_CRASH_EVERY_N", "0"))  # 0 = disabled
CHAOS_SLOW_JOBS = os.getenv("CHAOS_SLOW_JOBS", "false").lower() == "true"

_start_time = time.time()
_jobs_processed = 0
# Fake in-memory queue for demo
_job_queue: list[dict] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("worker-service starting up")
    # Seed some fake jobs
    for i in range(10):
        _job_queue.append({"id": f"job-{i}", "type": "send_email", "payload": {"order_id": f"order-{i}"}})
    QUEUE_DEPTH.set(len(_job_queue))

    # Start background job processor
    asyncio.create_task(process_jobs())
    yield
    logger.info("worker-service shutting down")


app = FastAPI(
    title="Worker Service",
    description="Background job processor for AutoOps demo app",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class JobSubmit(BaseModel):
    type: str
    payload: dict


@app.get("/health", tags=["Health"])
async def health():
    return {
        "status": "healthy",
        "service": "worker-service",
        "uptime_seconds": round(time.time() - _start_time, 2),
        "jobs_processed": _jobs_processed,
        "queue_depth": len(_job_queue),
        "chaos_crash_every_n": CHAOS_CRASH_EVERY_N,
    }

@app.get("/ready", tags=["Health"])
async def ready():
    return {"status": "ready"}

@app.get("/metrics", tags=["Observability"])
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/jobs", status_code=202, tags=["Jobs"])
async def submit_job(body: JobSubmit):
    """Submit a job to the queue."""
    job = {"id": f"job-{int(time.time()*1000)}", "type": body.type, "payload": body.payload}
    _job_queue.append(job)
    QUEUE_DEPTH.set(len(_job_queue))
    logger.info(f"Job {job['id']} queued (type={body.type})")
    return {"job_id": job["id"], "status": "queued"}


@app.get("/jobs/stats", tags=["Jobs"])
async def job_stats():
    return {
        "queue_depth": len(_job_queue),
        "total_processed": _jobs_processed,
    }


async def process_jobs():
    """
    Background task: continuously pulls jobs from queue and processes them.

    CHAOS_CRASH_EVERY_N: simulates a CrashLoopBackOff.
    When enabled, the worker raises an exception every N jobs,
    causing the FastAPI process to crash. Kubernetes sees the pod
    exit and restarts it — but if it keeps crashing, K8s marks it
    as CrashLoopBackOff and backs off exponentially.
    """
    global _jobs_processed

    logger.info("Job processor started")
    while True:
        if _job_queue:
            job = _job_queue.pop(0)
            QUEUE_DEPTH.set(len(_job_queue))

            start = time.time()
            try:
                # Simulate job work
                work_time = random.uniform(0.5, 2.0)
                if CHAOS_SLOW_JOBS:
                    work_time *= 5
                await asyncio.sleep(work_time)

                _jobs_processed += 1
                JOB_DURATION.observe(time.time() - start)
                JOBS_PROCESSED.labels(status="success").inc()
                logger.info(f"Job {job['id']} completed in {work_time:.2f}s")

                # Chaos: crash after every N jobs
                if CHAOS_CRASH_EVERY_N > 0 and _jobs_processed % CHAOS_CRASH_EVERY_N == 0:
                    WORKER_RESTARTS.inc()
                    logger.error(f"CHAOS: crashing after {_jobs_processed} jobs")
                    raise RuntimeError("Chaos-induced crash")

            except RuntimeError:
                raise  # let it crash
            except Exception as e:
                JOBS_PROCESSED.labels(status="failure").inc()
                logger.error(f"Job {job['id']} failed: {e}")
        else:
            # Queue empty, wait before checking again
            await asyncio.sleep(1)
