"""
Scenario suite (test layer 1).

Exercises queueing, priority, cancellation, validation, retention and the
legacy contract against the mock model servers. No cluster or GPU required.

    # terminal 1
    python tests/mock_models.py --port 9100
    # terminal 2
    set -a; . tests/.env.local; set +a; python predictor.py
    # terminal 3
    python tests/test_scenarios.py
"""

import asyncio
import base64
import sys
import time
from pathlib import Path
from typing import List, Tuple

import httpx

BASE_URL = "http://127.0.0.1:8080"
MOCK_URL = "http://127.0.0.1:9100"
FIXTURES = Path(__file__).parent / "fixtures"

BIG = FIXTURES / "10-long-pdf" / "DR-1-45.pdf"
SMALL = FIXTURES / "1-born-digital-plain-prose" / "31530_schlichtungskommission_formular.pdf"
ENCRYPTED = FIXTURES / "11-pw-protected" / "fixture11_encrypted.pdf"
CORRUPT = FIXTURES / "12-corrupt-truncated" / "fixture12_truncated.pdf"
ZERO_PAGE = FIXTURES / "13-empty" / "13_zero_page.pdf"

_results: List[Tuple[str, bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    _results.append((name, passed, detail))
    mark = "PASS" if passed else "FAIL"
    print(f"  [{mark}] {name}" + (f" - {detail}" if detail else ""))


async def submit(client: httpx.AsyncClient, pdf: Path, user: str, title: str):
    files = {"file": (pdf.name, pdf.read_bytes(), "application/pdf")}
    return await client.post(
        "/jobs", files=files, data={"user_uuid": user, "doc_title": title}
    )


async def wait_done(client: httpx.AsyncClient, job_id: str, user: str, timeout=180.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = await client.get(f"/jobs/{job_id}", params={"user_uuid": user})
        info = response.json()
        if info["status"] in ("completed", "completed_with_errors", "failed", "cancelled"):
            return info
        await asyncio.sleep(0.3)
    raise TimeoutError(job_id)


# --------------------------------------------------------------------------
async def test_happy_path(client):
    print("\n1. Happy path: submit -> poll -> result")
    response = await submit(client, SMALL, "user-a", "Happy Path")
    check("submit returns 202", response.status_code == 202, f"got {response.status_code}")

    job = response.json()
    check("job_id is instance-prefixed", "-" in job["job_id"])
    check("pages_total reported", job["pages_total"] == 4, f"got {job['pages_total']}")
    check("small doc gets high priority", job["priority"] == "high")

    final = await wait_done(client, job["job_id"], "user-a")
    check("job completes", final["status"] == "completed", final["status"])
    check("all pages processed", final["pages_done"] == final["pages_total"])

    response = await client.get(
        f"/jobs/{job['job_id']}/result", params={"user_uuid": "user-a"}
    )
    check("result returns 200", response.status_code == 200)

    payload = response.json()
    documents = payload["documents"]
    check("documents produced", len(documents) > 0, f"{len(documents)} docs")

    doc = documents[0]
    check(
        "document shape unchanged",
        {"content", "metadata", "embedding"} <= set(doc.keys()),
        str(sorted(doc.keys())),
    )
    meta = doc["metadata"]
    check(
        "metadata populated",
        bool(meta.get("language")) and bool(meta.get("summary")),
        f"lang={meta.get('language')!r}",
    )
    check("embedding is non-empty", bool(doc["embedding"]))


async def test_result_not_ready(client):
    print("\n2. Result before completion returns 409")
    response = await submit(client, BIG, "user-a", "Not Ready")
    job = response.json()

    response = await client.get(
        f"/jobs/{job['job_id']}/result", params={"user_uuid": "user-a"}
    )
    check("409 while running", response.status_code == 409, f"got {response.status_code}")
    check("409 body carries progress", "pages_total" in response.json())

    await client.delete(f"/jobs/{job['job_id']}", params={"user_uuid": "user-a"})


async def test_cancellation(client):
    print("\n3. Cancellation")
    response = await submit(client, BIG, "user-a", "To Cancel")
    job = response.json()
    await asyncio.sleep(1.5)

    response = await client.delete(f"/jobs/{job['job_id']}", params={"user_uuid": "user-a"})
    check("cancel returns 200", response.status_code == 200)

    body = response.json()
    check("status is cancelled", body["status"] == "cancelled")
    check(
        "cancelled before finishing",
        body["pages_processed"] < body["pages_total"],
        f"{body['pages_processed']}/{body['pages_total']}",
    )

    processed_at_cancel = body["pages_processed"]
    await asyncio.sleep(3.0)

    response = await client.get(f"/jobs/{job['job_id']}", params={"user_uuid": "user-a"})
    after = response.json()
    check(
        "work stops promptly after cancel",
        after["pages_done"] - processed_at_cancel <= 6,
        f"{processed_at_cancel} -> {after['pages_done']}",
    )

    response = await client.get(
        f"/jobs/{job['job_id']}/result", params={"user_uuid": "user-a"}
    )
    check("result on cancelled job is 409", response.status_code == 409)


async def test_worker_pool_survives_cancellation(client):
    """
    Regression test.

    Cancelling a job used to raise asyncio.CancelledError inside the page
    task. That derives from BaseException, so it escaped `except Exception`
    and killed the worker task outright. Two cancelled documents were enough
    to kill the whole pool, after which every subsequent upload sat in
    `queued` forever with no error anywhere.
    """
    print("\n3b. Worker pool survives repeated cancellations")

    before = (await client.get("/stats")).json()["worker_pool"]

    for i in range(3):
        response = await submit(client, BIG, "user-a", f"Cancel Storm {i}")
        job = response.json()
        await asyncio.sleep(1.2)
        await client.delete(f"/jobs/{job['job_id']}", params={"user_uuid": "user-a"})

    await asyncio.sleep(2.0)

    after = (await client.get("/stats")).json()["worker_pool"]
    check(
        "all workers still alive after cancellations",
        after.get("workers_alive") == after.get("workers"),
        f"{after.get('workers_alive')}/{after.get('workers')} alive",
    )
    check("pool reports healthy", after.get("healthy") is True)
    check(
        "worker count unchanged",
        after.get("workers") == before.get("workers"),
        f"{before.get('workers')} -> {after.get('workers')}",
    )

    # The real proof: a new job must still complete.
    response = await submit(client, SMALL, "user-a", "After Cancel Storm")
    job = response.json()
    final = await wait_done(client, job["job_id"], "user-a", timeout=60.0)
    check(
        "queue still drains after cancellations",
        final["status"] == "completed",
        final["status"],
    )


async def test_ownership(client):
    print("\n4. Ownership isolation")
    response = await submit(client, SMALL, "owner", "Private Doc")
    job = response.json()
    await wait_done(client, job["job_id"], "owner")

    response = await client.get(f"/jobs/{job['job_id']}", params={"user_uuid": "attacker"})
    check("other user gets 404 on status", response.status_code == 404)

    response = await client.get(
        f"/jobs/{job['job_id']}/result", params={"user_uuid": "attacker"}
    )
    check("other user gets 404 on result", response.status_code == 404)

    response = await client.get(f"/jobs/{job['job_id']}", params={"user_uuid": "owner"})
    check("owner still has access", response.status_code == 200)


async def test_unknown_and_lost(client):
    print("\n5. Unknown vs lost job ids")
    response = await client.get("/jobs/deadbeef-doesnotexist", params={"user_uuid": "u"})
    check("foreign instance id -> 410 Gone", response.status_code == 410, f"got {response.status_code}")
    check("410 body flags resubmit", response.json().get("code") == "job_lost")

    stats = (await client.get("/stats")).json()
    instance = stats["store"]["instance_id"]
    response = await client.get(f"/jobs/{instance}-nosuchjob", params={"user_uuid": "u"})
    check("same instance, unknown id -> 404", response.status_code == 404)


async def test_validation(client):
    print("\n6. Input validation")
    cases = [
        (ENCRYPTED, "encrypted PDF", 422, "encrypted_pdf"),
        (ZERO_PAGE, "zero-page PDF", 400, "empty_pdf"),
    ]
    for path, label, expected_status, expected_code in cases:
        if not path.exists():
            check(f"{label} fixture present", False, f"missing {path}")
            continue
        response = await submit(client, path, "user-a", label)
        ok = response.status_code == expected_status
        detail = f"got {response.status_code} {response.json().get('code')}"
        check(f"{label} rejected with {expected_status}", ok, detail)
        if expected_code and ok:
            check(f"{label} error code", response.json().get("code") == expected_code)

    # A truncated file that PyMuPDF can repair should be salvaged, not
    # rejected: recovering the content is more useful than failing the upload.
    # The repair is logged as a warning by preprocessing.render.
    if CORRUPT.exists():
        response = await submit(client, CORRUPT, "user-a", "Truncated")
        check(
            "repairable truncated PDF is salvaged",
            response.status_code == 202,
            f"got {response.status_code}",
        )
        if response.status_code == 202:
            job = response.json()
            final = await wait_done(client, job["job_id"], "user-a")
            check(
                "repaired PDF still yields content",
                final["status"] in ("completed", "completed_with_errors"),
                final["status"],
            )

    files = {"file": ("empty.pdf", b"", "application/pdf")}
    response = await client.post(
        "/jobs", files=files, data={"user_uuid": "u", "doc_title": "Empty"}
    )
    check("empty upload rejected", response.status_code == 400, f"got {response.status_code}")

    files = {"file": ("garbage.pdf", b"this is definitely not a pdf" * 10, "application/pdf")}
    response = await client.post(
        "/jobs", files=files, data={"user_uuid": "u", "doc_title": "Garbage"}
    )
    check("non-PDF bytes rejected", response.status_code == 400, f"got {response.status_code}")


async def test_legacy_contract(client):
    print("\n7. Legacy v1 :predict contract")
    payload = {
        "instances": [
            {
                "data_url": base64.b64encode(SMALL.read_bytes()).decode(),
                "user_uuid": "legacy-user",
                "doc_title": "Legacy Doc",
            }
        ]
    }
    response = await client.post(
        "/v1/models/user-pdf-preprocessing:predict", json=payload, timeout=120.0
    )
    check("legacy small doc returns 200", response.status_code == 200, f"got {response.status_code}")

    if response.status_code == 200:
        body = response.json()
        check("response has 'documents'", "documents" in body)
        check("documents non-empty", len(body["documents"]) > 0)
        doc = body["documents"][0]
        check(
            "legacy doc shape unchanged",
            {"content", "metadata", "embedding"} <= set(doc.keys()),
        )

    payload["instances"][0]["data_url"] = base64.b64encode(BIG.read_bytes()).decode()
    response = await client.post(
        "/v1/models/user-pdf-preprocessing:predict", json=payload, timeout=120.0
    )
    check("legacy large doc returns 413", response.status_code == 413, f"got {response.status_code}")

    if response.status_code == 413:
        body = response.json()
        check("413 points at async API", body.get("code") == "use_async_api")
        check("413 gives submit_url", body.get("submit_url") == "/jobs")

    response = await client.post("/v1/models/user-pdf-preprocessing:predict", json={})
    check("legacy bad payload -> 400", response.status_code == 400)


async def test_priority_and_concurrency(client):
    print("\n8. Priority ordering and VLM concurrency cap")
    async with httpx.AsyncClient(base_url=MOCK_URL) as mock:
        await mock.post("/mock/reset")

    t0 = time.time()
    response = await submit(client, BIG, "user-a", "Big Contention")
    big = response.json()
    check("large doc gets low priority", big["priority"] == "low")

    await asyncio.sleep(1.5)

    smalls = []
    for i in range(5):
        response = await submit(client, SMALL, "user-a", f"Small {i}")
        smalls.append(response.json())

    async def finish(job):
        await wait_done(client, job["job_id"], "user-a")
        return time.time() - t0

    times = await asyncio.gather(finish(big), *[finish(s) for s in smalls])
    big_time, small_times = times[0], times[1:]

    check(
        "small docs overtake the large doc",
        max(small_times) < big_time,
        f"smalls max {max(small_times):.1f}s vs big {big_time:.1f}s",
    )

    async with httpx.AsyncClient(base_url=MOCK_URL) as mock:
        stats = (await mock.get("/mock/stats")).json()

    check(
        "VLM concurrency cap respected",
        stats["vlm_peak"] <= 4,
        f"peak {stats['vlm_peak']}, cap 4",
    )
    priorities = set(stats["priorities_seen"])
    check("vLLM priority field sent", bool(priorities), f"values {sorted(priorities)}")
    check(
        "all OCR priorities below translation (0)",
        all(p > 0 for p in priorities),
        f"values {sorted(priorities)}",
    )


async def test_stats(client):
    print("\n9. Stats endpoint")
    response = await client.get("/stats")
    check("stats returns 200", response.status_code == 200)
    body = response.json()
    for key in ("queue", "vlm_inflight", "vlm_max_concurrency", "worker_pool", "store"):
        check(f"stats has '{key}'", key in body)


async def main() -> int:
    missing = [p for p in (BIG, SMALL) if not p.exists()]
    if missing:
        print(f"Missing required fixtures: {missing}", file=sys.stderr)
        return 2

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=180.0) as client:
        try:
            await client.get("/healthz")
        except httpx.ConnectError:
            print(f"Service not reachable at {BASE_URL}", file=sys.stderr)
            return 2

        print("=" * 70)
        print("pdf-ocr-pipeline scenario suite")
        print("=" * 70)

        await test_happy_path(client)
        await test_result_not_ready(client)
        await test_cancellation(client)
        await test_worker_pool_survives_cancellation(client)
        await test_ownership(client)
        await test_unknown_and_lost(client)
        await test_validation(client)
        await test_legacy_contract(client)
        await test_priority_and_concurrency(client)
        await test_stats(client)

    passed = sum(1 for _, ok, _ in _results if ok)
    total = len(_results)
    print("\n" + "=" * 70)
    print(f"{passed}/{total} checks passed")
    if passed < total:
        print("\nFailures:")
        for name, ok, detail in _results:
            if not ok:
                print(f"  - {name}: {detail}")
    print("=" * 70)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
