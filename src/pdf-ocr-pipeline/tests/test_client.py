"""
Reference client for the asynchronous /jobs API.

Mirrors what the Java application will do: multipart upload, poll for status,
fetch the result. Doubles as a manual smoke test against a deployed pod.

    # Async API against a deployed service
    python tests/test_client.py \
        --base-url https://pdf-ocr-pipeline-model-serving.apps.openshift-ai.mgnt.zas.admin.ch \
        --pdf tests/fixtures/1-born-digital-plain-prose/31530_schlichtungskommission_formular.pdf \
        --token "$TOKEN"

    # Legacy v1 contract
    python tests/test_client.py --base-url ... --pdf ... --legacy

    # Submit then cancel after 5s
    python tests/test_client.py --base-url ... --pdf ... --cancel-after 5
"""

import argparse
import asyncio
import base64
import json
import sys
import time
from pathlib import Path

import httpx


class PdfOcrClient:
    def __init__(self, base_url: str, token: str = "", verify: bool = False):
        self.base_url = base_url.rstrip("/")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = httpx.AsyncClient(
            base_url=self.base_url, headers=headers, verify=verify, timeout=600.0
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self._client.aclose()

    async def submit(self, pdf_path: Path, user_uuid: str, doc_title: str) -> dict:
        files = {"file": (pdf_path.name, pdf_path.read_bytes(), "application/pdf")}
        data = {"user_uuid": user_uuid, "doc_title": doc_title}
        response = await self._client.post("/jobs", files=files, data=data)
        response.raise_for_status()
        return response.json()

    async def status(self, job_id: str, user_uuid: str) -> dict:
        response = await self._client.get(
            f"/jobs/{job_id}", params={"user_uuid": user_uuid}
        )
        response.raise_for_status()
        return response.json()

    async def result(self, job_id: str, user_uuid: str) -> dict:
        response = await self._client.get(
            f"/jobs/{job_id}/result", params={"user_uuid": user_uuid}
        )
        response.raise_for_status()
        return response.json()

    async def cancel(self, job_id: str, user_uuid: str) -> dict:
        response = await self._client.delete(
            f"/jobs/{job_id}", params={"user_uuid": user_uuid}
        )
        response.raise_for_status()
        return response.json()

    async def stats(self) -> dict:
        response = await self._client.get("/stats")
        response.raise_for_status()
        return response.json()

    async def wait(
        self,
        job_id: str,
        user_uuid: str,
        poll_interval: float = 2.0,
        timeout: float = 3600.0,
        verbose: bool = True,
    ) -> dict:
        """Poll until terminal. This is the loop the Java app implements."""
        deadline = time.time() + timeout
        last_done = -1

        while time.time() < deadline:
            info = await self.status(job_id, user_uuid)

            if verbose and info["pages_done"] != last_done:
                pct = (
                    100.0 * info["pages_done"] / info["pages_total"]
                    if info["pages_total"]
                    else 0.0
                )
                print(
                    f"  [{info['status']:<22}] "
                    f"{info['pages_done']}/{info['pages_total']} pages ({pct:.0f}%)"
                )
                last_done = info["pages_done"]

            if info["status"] in (
                "completed",
                "completed_with_errors",
                "failed",
                "cancelled",
            ):
                return info

            await asyncio.sleep(poll_interval)

        raise TimeoutError(f"Job {job_id} did not finish within {timeout}s")

    async def legacy_predict(
        self, pdf_path: Path, user_uuid: str, doc_title: str, model: str
    ) -> httpx.Response:
        payload = {
            "instances": [
                {
                    "data_url": base64.b64encode(pdf_path.read_bytes()).decode(),
                    "user_uuid": user_uuid,
                    "doc_title": doc_title,
                }
            ]
        }
        return await self._client.post(f"/v1/models/{model}:predict", json=payload)


def _summarise(documents: list) -> None:
    print(f"\nReceived {len(documents)} document(s)")
    for i, doc in enumerate(documents[:3]):
        meta = doc.get("metadata", {})
        embedding = doc.get("embedding", "")
        dims = len(embedding.split(",")) if embedding else 0
        print(
            f"  [{i}] pages={meta.get('page_num')} lang={meta.get('language')!r} "
            f"chars={len(doc.get('content', ''))} embedding_dims={dims}"
        )
        print(f"      summary: {str(meta.get('summary'))[:100]}")
    if len(documents) > 3:
        print(f"  ... and {len(documents) - 3} more")


async def run(args: argparse.Namespace) -> int:
    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"PDF not found: {pdf_path}", file=sys.stderr)
        return 1

    async with PdfOcrClient(args.base_url, args.token, verify=args.verify) as client:
        if args.legacy:
            print(f"Legacy :predict with {pdf_path.name} ...")
            started = time.time()
            response = await client.legacy_predict(
                pdf_path, args.user_uuid, args.doc_title, args.model
            )
            elapsed = time.time() - started
            print(f"HTTP {response.status_code} in {elapsed:.1f}s")
            if response.status_code == 200:
                _summarise(response.json().get("documents", []))
            else:
                print(json.dumps(response.json(), indent=2))
            return 0 if response.status_code == 200 else 1

        print(f"Submitting {pdf_path.name} ({pdf_path.stat().st_size / 1024:.0f} KB) ...")
        started = time.time()
        submission = await client.submit(pdf_path, args.user_uuid, args.doc_title)
        print(json.dumps(submission, indent=2))

        job_id = submission["job_id"]

        if args.cancel_after:
            await asyncio.sleep(args.cancel_after)
            print(f"\nCancelling after {args.cancel_after}s ...")
            print(json.dumps(await client.cancel(job_id, args.user_uuid), indent=2))
            return 0

        print("\nPolling ...")
        final = await client.wait(job_id, args.user_uuid, poll_interval=args.poll_interval)
        elapsed = time.time() - started

        print(f"\nFinished as {final['status']} in {elapsed:.1f}s")
        if final.get("pages_failed"):
            print(f"Failed pages: {final['pages_failed']}")

        if final["status"] in ("completed", "completed_with_errors"):
            payload = await client.result(job_id, args.user_uuid)
            _summarise(payload["documents"])
            if args.out:
                Path(args.out).write_text(json.dumps(payload, indent=2))
                print(f"\nWrote result to {args.out}")
            return 0

        print(f"Error: {final.get('error')}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="pdf-ocr-pipeline reference client")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--user-uuid", default="test-user")
    parser.add_argument("--doc-title", default="Test Document")
    parser.add_argument("--token", default="")
    parser.add_argument("--model", default="user-pdf-preprocessing")
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--cancel-after", type=float, default=0.0)
    parser.add_argument("--legacy", action="store_true", help="use the v1 :predict path")
    parser.add_argument("--verify", action="store_true", help="verify TLS certificates")
    parser.add_argument("--out", help="write the result JSON to this path")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
