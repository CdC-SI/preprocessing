"""
Translation contention benchmark (test layer 4).

Measures translation latency percentiles while the OCR pipeline is idle, and
again while a large PDF is being processed. This is the acceptance test for
the whole change: translation p95 under load should stay within ~20% of the
idle baseline.

Run the baseline BEFORE deploying the new pipeline so the improvement is
demonstrable.

    export ZIA_TRANSLATION_URL="https://gateway-r.zas.admin.ch/zia-trad/api/translation"
    export ZIA_TRANSLATION_TOKEN="..."
    export PDF_OCR_URL="https://pdf-ocr-pipeline-model-serving.apps.openshift-ai.mgnt.zas.admin.ch"
    export AUTH_TOKEN="..."

    # A: baseline, no OCR load
    python tests/benchmark_contention.py --phase idle --duration 60

    # B: while a large PDF is processed (async API)
    python tests/benchmark_contention.py --phase load \
        --pdf tests/fixtures/10-long-pdf/DR-1-45.pdf

    # C: same, against the OLD synchronous pipeline
    python tests/benchmark_contention.py --phase load --legacy \
        --pdf tests/fixtures/10-long-pdf/DR-1-45.pdf
"""

import argparse
import asyncio
import base64
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import httpx

TRANSLATION_URL = os.environ.get("ZIA_TRANSLATION_URL", "")
TRANSLATION_TOKEN = os.environ.get("ZIA_TRANSLATION_TOKEN", "")
PDF_OCR_URL = os.environ.get("PDF_OCR_URL", "http://127.0.0.1:8080")
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "")

SAMPLE_TEXTS = [
    "Le présent document décrit les prestations de l'assurance-vieillesse.",
    "Die Anmeldung muss innerhalb von dreissig Tagen eingereicht werden.",
    "Il richiedente deve presentare la documentazione completa.",
    "The applicant must provide supporting evidence with the claim form.",
]


@dataclass
class Latencies:
    values: List[float] = field(default_factory=list)
    errors: int = 0

    def add(self, value: float) -> None:
        self.values.append(value)

    def percentiles(self) -> dict:
        if not self.values:
            return {"count": 0, "errors": self.errors}
        ordered = sorted(self.values)

        def pct(p: float) -> float:
            index = min(int(len(ordered) * p), len(ordered) - 1)
            return round(ordered[index], 3)

        return {
            "count": len(ordered),
            "errors": self.errors,
            "min": round(ordered[0], 3),
            "p50": pct(0.50),
            "p95": pct(0.95),
            "p99": pct(0.99),
            "max": round(ordered[-1], 3),
            "mean": round(statistics.mean(ordered), 3),
        }


async def translate_once(client: httpx.AsyncClient, text: str, latencies: Latencies):
    """
    One translation request.

    The ZIA translation gateway authenticates with a custom `Blue` header
    rather than `Authorization`.
    """
    started = time.time()
    try:
        response = await client.post(
            TRANSLATION_URL,
            json={"text": text, "target_language": "de"},
            headers={
                "Blue": f"Bearer {TRANSLATION_TOKEN}",
                "Content-Type": "application/json",
            },
            timeout=120.0,
        )
        elapsed = time.time() - started
        if response.status_code == 200:
            latencies.add(elapsed)
        else:
            latencies.errors += 1
            print(f"    translation HTTP {response.status_code}", file=sys.stderr)
    except Exception as exc:
        latencies.errors += 1
        print(f"    translation error: {type(exc).__name__}: {exc}", file=sys.stderr)


async def translation_load(
    duration: float, rate: float, stop_event: asyncio.Event
) -> Latencies:
    """Drive translation requests at a steady rate until told to stop."""
    latencies = Latencies()
    interval = 1.0 / rate
    index = 0

    async with httpx.AsyncClient(verify=False) as client:
        pending: List[asyncio.Task] = []
        deadline = time.time() + duration

        while time.time() < deadline and not stop_event.is_set():
            text = SAMPLE_TEXTS[index % len(SAMPLE_TEXTS)]
            index += 1
            pending.append(asyncio.create_task(translate_once(client, text, latencies)))
            pending = [task for task in pending if not task.done()]
            await asyncio.sleep(interval)

        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    return latencies


async def drive_ocr_async(pdf: Path, stop_event: asyncio.Event) -> dict:
    """Submit a PDF through the async /jobs API and poll to completion."""
    headers = {"Authorization": f"Bearer {AUTH_TOKEN}"} if AUTH_TOKEN else {}
    started = time.time()

    async with httpx.AsyncClient(base_url=PDF_OCR_URL, headers=headers, verify=False, timeout=120.0) as client:
        files = {"file": (pdf.name, pdf.read_bytes(), "application/pdf")}
        data = {"user_uuid": "benchmark", "doc_title": "Contention Benchmark"}
        response = await client.post("/jobs", files=files, data=data)
        response.raise_for_status()
        job = response.json()
        print(f"  OCR job {job['job_id']} submitted ({job['pages_total']} pages, {job['priority']} priority)")

        while True:
            response = await client.get(
                f"/jobs/{job['job_id']}", params={"user_uuid": "benchmark"}
            )
            info = response.json()
            if info["status"] in ("completed", "completed_with_errors", "failed", "cancelled"):
                break
            await asyncio.sleep(2.0)

    stop_event.set()
    elapsed = time.time() - started
    print(f"  OCR finished as {info['status']} in {elapsed:.1f}s")
    return {"status": info["status"], "seconds": round(elapsed, 1), "pages": info["pages_total"]}


async def drive_ocr_legacy(pdf: Path, stop_event: asyncio.Event) -> dict:
    """Submit through the old synchronous :predict path, for comparison."""
    headers = {"Authorization": f"Bearer {AUTH_TOKEN}"} if AUTH_TOKEN else {}
    payload = {
        "instances": [
            {
                "data_url": base64.b64encode(pdf.read_bytes()).decode(),
                "user_uuid": "benchmark",
                "doc_title": "Contention Benchmark (legacy)",
            }
        ]
    }
    started = time.time()

    async with httpx.AsyncClient(base_url=PDF_OCR_URL, headers=headers, verify=False, timeout=3600.0) as client:
        try:
            response = await client.post(
                "/v1/models/user-pdf-preprocessing:predict", json=payload
            )
            status = str(response.status_code)
        except Exception as exc:
            status = f"error: {type(exc).__name__}"

    stop_event.set()
    elapsed = time.time() - started
    print(f"  legacy :predict returned {status} in {elapsed:.1f}s")
    return {"status": status, "seconds": round(elapsed, 1)}


async def main() -> int:
    parser = argparse.ArgumentParser(description="Translation contention benchmark")
    parser.add_argument("--phase", choices=["idle", "load"], required=True)
    parser.add_argument("--pdf", help="PDF to process during the 'load' phase")
    parser.add_argument("--duration", type=float, default=300.0, help="max seconds")
    parser.add_argument("--rate", type=float, default=2.0, help="translation req/s")
    parser.add_argument("--legacy", action="store_true", help="drive OCR via :predict")
    parser.add_argument("--out", help="write results JSON here")
    args = parser.parse_args()

    if not TRANSLATION_URL:
        print("ZIA_TRANSLATION_URL is not set", file=sys.stderr)
        return 2

    stop_event = asyncio.Event()
    print(f"\nPhase: {args.phase} | translation rate: {args.rate}/s")
    print("-" * 60)

    if args.phase == "idle":
        latencies = await translation_load(args.duration, args.rate, stop_event)
        ocr_result: Optional[dict] = None
    else:
        if not args.pdf:
            print("--pdf is required for the 'load' phase", file=sys.stderr)
            return 2
        pdf = Path(args.pdf)
        if not pdf.exists():
            print(f"PDF not found: {pdf}", file=sys.stderr)
            return 2

        driver = drive_ocr_legacy if args.legacy else drive_ocr_async
        latencies, ocr_result = await asyncio.gather(
            translation_load(args.duration, args.rate, stop_event),
            driver(pdf, stop_event),
        )

    stats = latencies.percentiles()
    print("\nTranslation latency (seconds)")
    print(json.dumps(stats, indent=2))
    if ocr_result:
        print("\nOCR job")
        print(json.dumps(ocr_result, indent=2))

    if args.out:
        Path(args.out).write_text(
            json.dumps(
                {"phase": args.phase, "legacy": args.legacy,
                 "translation": stats, "ocr": ocr_result},
                indent=2,
            )
        )
        print(f"\nWrote {args.out}")

    print(
        "\nAcceptance criterion: p95 in the 'load' phase should be within ~20% "
        "of the 'idle' baseline."
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
