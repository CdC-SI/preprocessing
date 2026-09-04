# PDF OCR Pipeline

A KServe inference service that converts uploaded PDFs into chunked, embedded
documents for the RAG vector database, using a VLM for page-level OCR and an
LLM for chunk summaries.

The VLM is **shared with the translation service**. Nearly every design
decision here follows from that single constraint: OCR must never starve
translation.

---

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/jobs` | Submit a PDF (multipart). Returns `202` + `job_id` immediately. |
| `GET` | `/jobs/{job_id}?user_uuid=` | Poll status and progress. |
| `GET` | `/jobs/{job_id}/result?user_uuid=` | Fetch documents once complete. |
| `DELETE` | `/jobs/{job_id}?user_uuid=` | Cancel (e.g. user deletes the upload). |
| `GET` | `/stats` | Queue depth, VLM pressure, retention counters. |
| `POST` | `/v1/models/{name}:predict` | **Legacy v1 contract**, unchanged shapes. |

### Quickstart (curl)

Common setup — run once per shell session:

```bash
cd src/pdf-ocr-pipeline
export NO_PROXY=$NO_PROXY,.mgnt.zas.admin.ch
HOST=pdf-ocr-pipeline-model-serving.apps.openshift-ai.mgnt.zas.admin.ch
TOKEN=$(grep '^AUTH_TOKEN=' .env | cut -d= -f2-)
PDF=data/test.pdf
USER=abc-123

# -k skips TLS verification of the cluster's internal CA; drop it if your
# workstation already trusts that CA.
CURL="curl -sk"
```

#### `POST /jobs` — submit a document

```bash
RESP=$($CURL -X POST https://$HOST/jobs \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@$PDF" \
  -F "user_uuid=$USER" \
  -F "doc_title=Test Document")
echo "$RESP" | python3 -m json.tool

JOB_ID=$(echo "$RESP" | python3 -c 'import sys,json; print(json.load(sys.stdin)["job_id"])')
echo "JOB_ID=$JOB_ID"
```

```json
{
  "job_id": "1a342a54-f99efe5fdd2847d7bce3637d2caa865a",
  "status": "queued",
  "pages_total": 45,
  "priority": "low",
  "poll_url": "/jobs/1a342a54-.../?user_uuid=abc-123",
  "result_url": "/jobs/1a342a54-.../result?user_uuid=abc-123"
}
```

#### `GET /jobs/{id}` — poll status

```bash
$CURL "https://$HOST/jobs/$JOB_ID?user_uuid=$USER" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

# or watch it advance:
watch -n 2 "$CURL 'https://$HOST/jobs/$JOB_ID?user_uuid=$USER' -H 'Authorization: Bearer $TOKEN' | python3 -m json.tool"
```

#### `GET /jobs/{id}/result` — fetch the result

Only valid once status is terminal; returns `409` if called too early.

```bash
$CURL -w "\nHTTP %{http_code}\n" \
  "https://$HOST/jobs/$JOB_ID/result?user_uuid=$USER" \
  -H "Authorization: Bearer $TOKEN" -o result.json
python3 -m json.tool result.json | head -50
```

#### `DELETE /jobs/{id}` — cancel a job

```bash
$CURL -X DELETE "https://$HOST/jobs/$JOB_ID?user_uuid=$USER" \
  -H "Authorization: Bearer $TOKEN" -w "\nHTTP %{http_code}\n"

# confirm status flips to cancelled
$CURL "https://$HOST/jobs/$JOB_ID?user_uuid=$USER" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

#### `GET /stats` — queue depth / VLM pressure

```bash
$CURL "https://$HOST/stats" -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

```json
{
  "queue": {"high": 0, "low": 0, "total": 0},
  "queue_oldest_wait_seconds": 0.0,
  "vlm_inflight": 0,
  "vlm_max_concurrency": 4,
  "large_doc_concurrency": 2,
  "worker_pool": {"workers": 6, "workers_alive": 6, "healthy": true, "active_workers": 0, "pages_processed": 194, "draining": false, "uptime_seconds": 71143.0},
  "store": {"jobs_tracked": 12, "jobs_by_status": {"completed": 10, "cancelled": 2}, "result_bytes": 251986, "result_bytes_limit": 2147483648, "instance_id": "cbf41d9b"}
}
```

#### Legacy `POST /v1/models/{name}:predict`

Still supported, but rejects documents over `LEGACY_MAX_PAGES` (default 10)
with `413 use_async_api`. Use a small PDF.

```bash
B64=$(base64 -w 0 tests/fixtures/1-born-digital-plain-prose/31530_schlichtungskommission_formular.pdf)

cat <<EOF > payload.json
{
  "instances": [{
    "data_url": "${B64}",
    "user_uuid": "$USER",
    "doc_title": "Legacy Test Document"
  }]
}
EOF

$CURL -w "\nHTTP %{http_code}\n" -X POST \
  https://$HOST/v1/models/user-pdf-preprocessing:predict \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d @payload.json
```

Expect `200` with inline chunked/embedded documents for a small PDF, or
`413`/`use_async_api` if the PDF exceeds `LEGACY_MAX_PAGES`.

#### Error-path smoke checks

```bash
# 409: result requested before completion
$CURL -w "\nHTTP %{http_code}\n" "https://$HOST/jobs/$JOB_ID/result?user_uuid=$USER" \
  -H "Authorization: Bearer $TOKEN"

# 401: missing/invalid token
$CURL -w "\nHTTP %{http_code}\n" "https://$HOST/stats" -H "Authorization: Bearer invalid"

# 404/410: unknown or expired job id
$CURL -w "\nHTTP %{http_code}\n" "https://$HOST/jobs/00000000-does-not-exist?user_uuid=$USER" \
  -H "Authorization: Bearer $TOKEN"
```

### Status values

| Status | Meaning |
|---|---|
| `queued` | Accepted, not yet started. |
| `running` | Pages being processed; `pages_done`/`pages_total` advance. |
| `completed` | All pages succeeded; result available. |
| `completed_with_errors` | Result available; see `pages_failed`. |
| `failed` | Processing failed; see `error`. |
| `cancelled` | Cancelled by the client. |

### Client responsibilities

Poll `GET /jobs/{id}` every 2–5s until the status is terminal, then fetch the
result. Handle these cases:

- **`409` on `/result`** — not finished yet, keep polling.
- **`410 Gone`, `code: job_lost`** — the service restarted and in-memory state
  was lost. **Resubmit the document.**
- **`410 Gone`, `code: result_expired`** — the result TTL elapsed before it was
  fetched. Resubmit.
- **`413`, `code: use_async_api`** — returned by the legacy `:predict` endpoint
  for documents over `LEGACY_MAX_PAGES`. Use `POST /jobs` instead.

Results are retained for `RESULT_TTL_SECONDS` (default 1 hour), so fetch them
promptly.

---

## How it protects translation

Four mechanisms, all necessary:

1. **Asynchronous API.** Requests return in milliseconds, so a long document
   can no longer hold an HTTP connection open until the frontend times out.
2. **Page-level work units.** A 500-page document becomes 500 small queue items
   rather than one long-running task, so it interleaves with other work instead
   of monopolising the workers.
3. **Priority queue.** Documents of `LARGE_DOC_PAGE_THRESHOLD` pages or fewer
   (default 5) are treated as interactive and served first.
4. **Concurrency budget + vLLM priority.** At most `VLM_MAX_CONCURRENCY`
   (default 4) OCR requests are in flight against the VLM (`--max-num-seqs=17`),
   and OCR requests are sent with lower priority than translation.

Measured results and acceptance-test methodology: see **[`BENCHMARKS.md`](BENCHMARKS.md)**
(non-technical summary: **[`MANAGEMENT_SUMMARY.md`](MANAGEMENT_SUMMARY.md)**).

---

## Single-replica constraint

**This service must run with exactly one replica.**

The job store, the work queue and the VLM concurrency budget are held
in-process. A second replica would break the OCR-pressure guarantees and route
status polls to pods that do not know the job.

`manifests/inferenceservice.yaml` pins `minReplicas: maxReplicas: 1` with
`Recreate` strategy.

## Scaling out

Scaling out requires an external job store and queue (Redis or equivalent).
`JobStore` in `jobs/store.py` is already an interface for exactly that; no Redis
instance was available in the cluster for this iteration.

### Consequences of in-memory state

- A pod restart loses in-flight jobs. Job ids are prefixed with a per-process
  instance id, so polling a job from a previous process returns `410 Gone` with
  `code: job_lost` rather than a misleading `404`.
- `terminationGracePeriodSeconds: 300` plus a graceful drain means planned
  rollouts usually finish in-flight work before exiting.

---

## Configuration

All tunables are environment variables, injected from the
`pdf-ocr-pipeline-env` secret.

### Model endpoints

| Variable | Description |
|---|---|
| `VLM_URL` / `VLM_MODEL_NAME` | OpenAI-compatible VLM endpoint |
| `LLM_URL` / `LLM_MODEL_NAME` | OpenAI-compatible LLM endpoint |
| `EMBEDDING_URL` / `EMBEDDING_MODEL_NAME` | OpenAI-compatible embedding endpoint |
| `AUTH_TOKEN` | Bearer token for all three, and for this service |

> `TOKENIZER_URL` / `TOKENIZER_MODEL_NAME` are **no longer used** — tokenization
> is now local (see `tokenizer/`).

### Concurrency and queueing

| Variable | Default | Notes |
|---|---|---|
| `VLM_MAX_CONCURRENCY` | `4` | Global cap on in-flight OCR requests. Raising this eats into translation capacity. |
| `OCR_LARGE_DOC_CONCURRENCY` | `2` | Sub-budget for large documents. |
| `WORKER_COUNT` | `VLM_MAX_CONCURRENCY + 2` | In-process workers. |
| `LARGE_DOC_PAGE_THRESHOLD` | `5` | At or below this, a document is interactive. |
| `AGING_PROMOTION_SECONDS` | `900` | Anti-starvation promotion for large documents. |
| `PAGE_MAX_ATTEMPTS` | `3` | Per-page retries with exponential backoff. |

### Chunking and embedding

| Variable | Default | Notes |
|---|---|---|
| `CHUNK_TOKEN_BUDGET` | `5000` | Working chunk size. **Main retrieval-quality lever.** |
| `MAX_EMBEDDING_TOKENS` | `32768` | Hard limit of Qwen3-Embedding-0.6B. |
| `SUMMARY_HEADROOM_TOKENS` | `1024` | Reserved for prefixed summaries; prevents silent truncation. |
| `EMBEDDING_BATCH_SIZE` | `8` | Chunks per embedding request. |

### Rendering

| Variable | Default | Notes |
|---|---|---|
| `PDF_RENDER_DPI` | `150` | **Biggest GPU-cost lever** — image tokens scale with resolution. Benchmark 110/130/150. |
| `JPEG_QUALITY` | `85` | Below ~80, artefacts begin to affect character recognition. |
| `MAX_IMAGE_EDGE_PX` | `2000` | Caps pathological page sizes. |

### Limits and retention

| Variable | Default |
|---|---|
| `MAX_UPLOAD_BYTES` | `10485760` (10 MB) |
| `MAX_PAGES` | `1000` |
| `LEGACY_MAX_PAGES` | `10` |
| `RESULT_TTL_SECONDS` | `3600` |
| `METADATA_TTL_SECONDS` | `86400` |
| `MAX_RESULT_BYTES` | `2147483648` (2 GB, LRU eviction) |

### Feature flags (Phase 4, currently off)

| Variable | Default |
|---|---|
| `TEXT_LAYER_EXTRACTION_ENABLED` | `false` |
| `CONTEXTUAL_RETRIEVAL_ENABLED` | `false` |

---

## Layout

```
pdf-ocr-pipeline/
├── predictor.py              # FastAPI app: v1 dataplane + /jobs API
├── preprocessing/
│   ├── config.py             # every tunable, one place
│   ├── tokenization.py       # bundled Qwen3 tokenizer (no network calls)
│   ├── render.py             # PDF validation + per-page JPEG rendering
│   ├── clients.py            # VLM/LLM/embedding clients + concurrency budget
│   └── chunking.py           # O(n) chunk packing, contextualisation, doc build
├── jobs/
│   ├── models.py             # Job, JobStatus, API schemas
│   ├── store.py              # JobStore interface + in-memory impl (TTL, LRU)
│   ├── queue.py              # priority heap, aging, cancellation
│   ├── worker.py             # worker pool, retries, finalisation
│   ├── service.py            # submit / cancel orchestration
│   └── routes.py             # HTTP handlers
├── tokenizer/                # bundled tokenizer.json (pod has no internet)
├── manifests/
├── BENCHMARKS.md             # contention test methodology + results log
├── MANAGEMENT_SUMMARY.md     # non-technical summary of the async rebuild
├── benchmark_results/        # gitignored — raw JSON from benchmark runs
└── tests/
    ├── mock_models.py           # fake VLM/LLM/embedding servers
    ├── test_client.py           # reference client (mirrors the Java app)
    ├── test_scenarios.py        # 51-check scenario suite
    └── benchmark_contention.py  # translation latency under OCR load
```

---

## Local development

```bash
pip install -r requirements.txt

# 1. mock upstreams
python tests/mock_models.py --port 9100 &

# 2. the service
set -a; . tests/.env.local; set +a
python predictor.py &

# 3. scenario suite
python tests/test_scenarios.py

# single document, end to end
python tests/test_client.py --pdf tests/fixtures/10-long-pdf/DR-1-45.pdf
```

### Offline wheels

The pod has no internet access, so dependencies install from pre-downloaded
wheels in the model bucket. Regenerate whenever `requirements.txt` changes,
using Python 3.11 to match the pod image:

```bash
pip download -r requirements.txt --dest wheels \
  --platform manylinux2014_x86_64 --python-version 3.11 --only-binary=:all:
```

---

## Fixed in this iteration

Three latent bugs surfaced while restructuring:

- **The document-level LLM call always failed on large PDFs.** It passed the
  entire document (298k tokens observed in production logs) against a 128k
  context, and the exception was swallowed — so `language` and `summary` were
  silently empty in the metadata of *every* chunk of *every* large document.
  Summarisation is now per chunk, always within context.
- **Chunking was O(n²).** The token count was recomputed over the whole
  accumulated chunk for each page, once per page, over the network. Each page is
  now tokenized once, locally, against a running sum.
- **All pages were rendered up front.** A 500-page render at 150 DPI holds
  several GB of bitmaps simultaneously. Pages are now rendered on demand.

---

## Test harnesses

- `tests/test_client.py` — reference client (submit/poll/result/cancel)
- `tests/test_scenarios.py` — end-to-end scenario suite
- `tests/benchmark_contention.py` — translation latency baseline vs OCR load
- `tests/mock_models.py` — mock OpenAI-compatible VLM/LLM/embedding endpoints

---


## Not yet implemented

**Phase 4 (efficiency):** text-layer extraction with quality heuristics, and
neighbour-summary contextual retrieval. Both are behind flags, default off.

**Phase 5 (object storage):** dropped; multipart upload is retained.
