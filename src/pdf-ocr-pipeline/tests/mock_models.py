"""
Mock VLM / LLM / embedding servers.

OpenAI-compatible endpoints with configurable latency, so the queueing,
priority, cancellation and retention behaviour can be exercised deterministically
on a laptop without touching the GPU cluster.

The server records peak concurrency per role, which is how the tests assert
that the VLM concurrency budget is actually being respected.

Run standalone:
    python tests/mock_models.py --port 9100 --vlm-latency 0.5
"""

import argparse
import asyncio
import json
import random
import time
from typing import Dict, List

import uvicorn
from fastapi import FastAPI, Request

app = FastAPI(title="mock-models")

SETTINGS = {
    "vlm_latency": 0.4,
    "llm_latency": 0.2,
    "embedding_latency": 0.05,
    "vlm_failure_rate": 0.0,
    "jitter": 0.15,
}

STATE = {
    "vlm_inflight": 0,
    "vlm_peak": 0,
    "vlm_calls": 0,
    "llm_calls": 0,
    "embedding_calls": 0,
    "priorities_seen": [],
    "translation_latencies": [],
}


def _latency(base: float) -> float:
    return max(0.0, base * (1.0 + random.uniform(-SETTINGS["jitter"], SETTINGS["jitter"])))


def _is_vlm(body: dict) -> bool:
    """A VLM request carries an image part in the user message."""
    for message in body.get("messages", []):
        content = message.get("content")
        if isinstance(content, list):
            if any(part.get("type") == "image_url" for part in content):
                return True
    return False


def _schema_response(body: dict) -> dict:
    """Produce a payload matching the requested json_schema."""
    fmt = body.get("response_format") or {}
    schema = (fmt.get("json_schema") or {}).get("schema") or {}
    props = set((schema.get("properties") or {}).keys())

    if "ocr_content" in props:
        return {
            "ocr_content": (
                "# Mock page\n\n"
                "Extracted body text for retrieval testing. "
                + "Filler sentence. " * random.randint(20, 60)
            )
        }
    if "summary" in props or "language" in props:
        return {
            "language": random.choice(["fr", "de", "it", "en"]),
            "summary": "Mock summary of the chunk contents in two short sentences.",
        }
    return {}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    is_vlm = _is_vlm(body)

    if "priority" in body:
        STATE["priorities_seen"].append(body["priority"])

    if is_vlm:
        STATE["vlm_inflight"] += 1
        STATE["vlm_peak"] = max(STATE["vlm_peak"], STATE["vlm_inflight"])
        STATE["vlm_calls"] += 1
    else:
        STATE["llm_calls"] += 1

    try:
        if is_vlm and random.random() < SETTINGS["vlm_failure_rate"]:
            await asyncio.sleep(0.05)
            return {"error": {"message": "mock upstream failure", "type": "ServerError"}}, 500

        await asyncio.sleep(
            _latency(SETTINGS["vlm_latency"] if is_vlm else SETTINGS["llm_latency"])
        )
        content = json.dumps(_schema_response(body))
        return {
            "id": "mock-1",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body.get("model", "mock"),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        }
    finally:
        if is_vlm:
            STATE["vlm_inflight"] -= 1


@app.post("/v1/embeddings")
async def embeddings(request: Request):
    body = await request.json()
    inputs = body.get("input")
    if isinstance(inputs, str):
        inputs = [inputs]
    STATE["embedding_calls"] += 1

    await asyncio.sleep(_latency(SETTINGS["embedding_latency"]))
    return {
        "object": "list",
        "model": body.get("model", "mock-embedding"),
        "data": [
            {
                "object": "embedding",
                "index": i,
                "embedding": [round(random.random(), 6) for _ in range(8)],
            }
            for i in range(len(inputs))
        ],
        "usage": {"prompt_tokens": 10, "total_tokens": 10},
    }


@app.post("/mock/translate")
async def translate(request: Request):
    """
    Stand-in for the translation service.

    Shares the mock VLM's inflight counter so that contention shows up exactly
    as it would on the real shared model server.
    """
    started = time.time()
    await asyncio.sleep(_latency(0.3))
    elapsed = time.time() - started
    STATE["translation_latencies"].append(elapsed)
    return {"translation": "mock translation", "latency": elapsed}


@app.get("/mock/stats")
async def stats() -> Dict:
    latencies: List[float] = sorted(STATE["translation_latencies"])
    percentile = (
        {
            "p50": latencies[len(latencies) // 2],
            "p95": latencies[int(len(latencies) * 0.95)],
        }
        if latencies
        else {}
    )
    return {**STATE, "translation_percentiles": percentile, "settings": SETTINGS}


@app.post("/mock/reset")
async def reset():
    STATE.update(
        vlm_inflight=0,
        vlm_peak=0,
        vlm_calls=0,
        llm_calls=0,
        embedding_calls=0,
        priorities_seen=[],
        translation_latencies=[],
    )
    return {"reset": True}


@app.post("/mock/settings")
async def update_settings(payload: dict):
    SETTINGS.update({k: v for k, v in payload.items() if k in SETTINGS})
    return SETTINGS


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9100)
    parser.add_argument("--vlm-latency", type=float, default=0.4)
    parser.add_argument("--llm-latency", type=float, default=0.2)
    parser.add_argument("--vlm-failure-rate", type=float, default=0.0)
    args = parser.parse_args()

    SETTINGS["vlm_latency"] = args.vlm_latency
    SETTINGS["llm_latency"] = args.llm_latency
    SETTINGS["vlm_failure_rate"] = args.vlm_failure_rate

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
