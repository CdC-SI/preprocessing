"""
Service entrypoint.

Serves the KServe v1 dataplane (so the existing `:predict` contract and the
readiness probes are unchanged) alongside the asynchronous /jobs API.

A plain FastAPI application is used rather than `kserve.ModelServer` because we
need custom routes and full control of the startup/shutdown lifecycle; the v1
endpoints KServe would have provided are re-implemented below and are only a
few lines each.

IMPORTANT: this service must run with exactly one replica. The job store, the
work queue and the VLM concurrency budget are all in-process, so a second
replica would silently double the OCR pressure on the shared VLM and route
status lookups to the wrong pod. The manifests enforce this.
"""

import logging
import os
import sys
from contextlib import asynccontextmanager

# The model bundle is mounted at /mnt/models and its subpackages are imported
# as top-level modules.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse

from preprocessing import clients, config, tokenization
from jobs import routes
from jobs.worker import get_pool

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("pdf-ocr-predictor")

MODEL_NAME = config.SERVICE_NAME


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "Starting %s (instance=%s, workers=%d, vlm_concurrency=%d, chunk_budget=%d)",
        MODEL_NAME,
        config.INSTANCE_ID,
        config.WORKER_COUNT,
        config.VLM_MAX_CONCURRENCY,
        config.effective_chunk_budget(),
    )

    # Fail loudly: without the bundled tokenizer we cannot chunk, and the pod
    # has no internet access to fetch one.
    if not tokenization.warm_up():
        logger.error("Tokenizer unavailable - chunking will fail. Check TOKENIZER_DIR.")

    clients.init_clients()
    await get_pool().start()

    app.state.ready = True
    yield

    logger.info("Shutdown signal received; draining")
    app.state.ready = False
    await get_pool().drain()
    await clients.close_clients()
    logger.info("Shutdown complete")


app = FastAPI(title="pdf-ocr-pipeline", lifespan=lifespan)
app.include_router(routes.router)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return routes.error_response(exc)


# --------------------------------------------------------------------------
# KServe v1 dataplane
# --------------------------------------------------------------------------
@app.get("/v1/models/{model_name}")
async def model_ready(model_name: str):
    return {"name": model_name, "ready": getattr(app.state, "ready", False)}


@app.post("/v1/models/{model_name}:predict")
async def predict(model_name: str, payload: dict):
    """Legacy synchronous contract. Unchanged request and response shapes."""
    return await routes.handle_legacy_predict(payload)


@app.get("/healthz")
@app.get("/healthz/ready")
@app.get("/v2/health/ready")
async def health_ready():
    if not getattr(app.state, "ready", False):
        return JSONResponse(status_code=503, content={"ready": False})
    return {"ready": True}


@app.get("/v2/health/live")
async def health_live():
    """
    Liveness.

    Reports unhealthy if any worker task has died. Without this a degraded pod
    would keep accepting uploads and silently never process them, which is a
    far worse failure mode than a restart.
    """
    pool = get_pool()
    if not getattr(app.state, "ready", False):
        return JSONResponse(status_code=503, content={"live": False, "reason": "not ready"})
    if not pool.healthy():
        logger.critical(
            "Liveness failing: %d/%d workers alive",
            pool.alive_workers(),
            len(pool._workers),
        )
        return JSONResponse(
            status_code=503,
            content={"live": False, "reason": "worker pool degraded", **pool.stats()},
        )
    return {"live": True}


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
        # Must stay 1: all state is in-process.
        workers=1,
        timeout_graceful_shutdown=int(os.environ.get("GRACEFUL_SHUTDOWN", "120")),
        log_level=config.LOG_LEVEL.lower(),
    )
