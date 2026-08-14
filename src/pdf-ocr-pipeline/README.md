# PDF OCR Pipeline# PDF OCR Pipeline



A KServe inference service that converts uploaded PDFs into chunked, embeddedA [KServe](https://kserve.github.io/website/) inference service that converts PDF documents into chunked, embedded documents ready for ingestion into a RAG vector database.

documents for the RAG vector database, using a VLM for page-level OCR and an

LLM for chunk summaries.## How it works



The VLM is **shared with the translation service**. Nearly every design1. **Render** — the PDF is decoded and each page is rasterised to a PNG image.

decision here follows from that single constraint: OCR must never starve2. **OCR** — pages are sent concurrently (up to 4 at a time) to a Vision-Language Model (VLM) which extracts text, tables, and figures as Markdown.

translation.3. **Chunk** — OCR'd pages are accumulated into chunks that stay within the embedding model's token limit, using a tokenizer endpoint for accurate counting.

4. **Summarise** — a single LLM call over the full text produces a document-level language tag and 2-3 sentence summary used to contextualise every chunk.

---5. **Embed** — all chunks are embedded in a single batch call to the embedding model.

6. **Return** — each chunk is returned as a document object containing content, metadata (title, language, summary, pages, …), and a pre-formatted embedding vector.

## API

## Environment variables

| Method | Path | Purpose |

|---|---|---|Copy `.env.example` to `.env` and fill in your endpoint URLs and model names.

| `POST` | `/jobs` | Submit a PDF (multipart). Returns `202` + `job_id` immediately. |

| `GET` | `/jobs/{job_id}?user_uuid=` | Poll status and progress. || Variable | Description |

| `GET` | `/jobs/{job_id}/result?user_uuid=` | Fetch documents once complete. ||---|---|

| `DELETE` | `/jobs/{job_id}?user_uuid=` | Cancel (e.g. user deletes the upload). || `VLM_URL` | OpenAI-compatible base URL for the Vision-Language Model |

| `GET` | `/stats` | Queue depth, VLM pressure, retention counters. || `VLM_MODEL_NAME` | Model name for the VLM |

| `POST` | `/v1/models/{name}:predict` | **Legacy v1 contract**, unchanged shapes. || `LLM_URL` | OpenAI-compatible base URL for the Language Model |

| `LLM_MODEL_NAME` | Model name for the LLM |

### Submitting| `EMBEDDING_URL` | OpenAI-compatible base URL for the embedding model |

| `EMBEDDING_MODEL_NAME` | Model name for the embedding model |

```bash| `TOKENIZE_URL` | Full URL of the vLLM `/tokenize` endpoint |

curl -X POST https://$HOST/jobs \| `TOKENIZE_MODEL_NAME` | Model name for the tokenizer |

  -H "Authorization: Bearer $TOKEN" \| `MAX_EMBEDDING_TOKENS` | Maximum tokens per chunk (default: `32768`) |

  -F "file=@document.pdf" \

  -F "user_uuid=abc-123" \## Local development

  -F "doc_title=My Document"

``````bash

pip install -r requirements.txt

```jsoncp .env.example .env  # fill in your values

{python predictor.py

  "job_id": "1a342a54-f99efe5fdd2847d7bce3637d2caa865a",```

  "status": "queued",

  "pages_total": 45,## Generating offline wheels

  "priority": "low",

  "poll_url": "/jobs/1a342a54-.../?user_uuid=abc-123",When the pod has no internet access or no trusted certificates, dependencies are installed from pre-downloaded wheels stored in the model bucket alongside the code. Generate them on a machine that **does** have internet access and the same Python version as the pod image (Python 3.11):

  "result_url": "/jobs/1a342a54-.../result?user_uuid=abc-123"

}```bash

```mkdir -p pdf-ocr-pipeline/wheels

pip download \

### Status values  -r pdf-ocr-pipeline/requirements.txt \

  --dest pdf-ocr-pipeline/wheels \

| Status | Meaning |  --platform manylinux2014_x86_64 \

|---|---|  --python-version 3.11 \

| `queued` | Accepted, not yet started. |  --only-binary=:all:

| `running` | Pages being processed; `pages_done`/`pages_total` advance. |```

| `completed` | All pages succeeded; result available. |

| `completed_with_errors` | Result available; see `pages_failed`. |The `wheels/` folder is then uploaded to S3 alongside the rest of the model files (see [Deploy on OpenShift](#deploy-on-openshift)). The ServingRuntime startup script detects the folder and installs from it:

| `failed` | Processing failed; see `error`. |

| `cancelled` | Cancelled by the client. |```bash

if [ -d /mnt/models/wheels ]; then

### Client responsibilities  pip install --no-index --find-links=/mnt/models/wheels ...

fi

Poll `GET /jobs/{id}` every 2–5s until the status is terminal, then fetch the```

result. Handle these cases:

## Deploy on OpenShift

- **`409` on `/result`** — not finished yet, keep polling.

- **`410 Gone`, `code: job_lost`** — the service restarted and in-memory state### Prerequisites

  was lost. **Resubmit the document.**

- **`410 Gone`, `code: result_expired`** — the result TTL elapsed before it was- OpenShift cluster with the **KServe** (or OpenShift AI / RHOAI) operator installed.

  fetched. Resubmit.- A container image built from this repo pushed to an accessible registry.

- **`413`, `code: use_async_api`** — returned by the legacy `:predict` endpoint

  for documents over `LEGACY_MAX_PAGES`. Use `POST /jobs` instead.### 1 — Build and push the image



Results are retained for `RESULT_TTL_SECONDS` (default 1 hour), so fetch them```bash

promptly.podman build -t <registry>/<org>/pdf-ocr-pipeline:latest .

podman push <registry>/<org>/pdf-ocr-pipeline:latest

---```



## How it protects translation### 2 — Create a Secret with environment variables



Four mechanisms, all necessary:```bash

oc create secret generic pdf-ocr-pipeline-env \

1. **Asynchronous API.** Requests return in milliseconds, so a long document  --from-literal=VLM_URL=https://your-vlm-endpoint/v1 \

   can no longer hold an HTTP connection open until the frontend times out.  --from-literal=VLM_MODEL_NAME=your-vlm-model \

  --from-literal=LLM_URL=https://your-llm-endpoint/v1 \

2. **Page-level work units.** A 500-page document becomes 500 small queue items  --from-literal=LLM_MODEL_NAME=your-llm-model \

   rather than one long-running task, so it interleaves with other work instead  --from-literal=EMBEDDING_URL=https://your-embedding-endpoint/v1 \

   of monopolising the workers. Progress, retries and cancellation all become  --from-literal=EMBEDDING_MODEL_NAME=your-embedding-model \

   naturally granular as a result.  --from-literal=TOKENIZE_URL=https://your-tokenizer-endpoint/tokenize \

  --from-literal=TOKENIZE_MODEL_NAME=your-tokenizer-model \

3. **Priority queue.** Documents of `LARGE_DOC_PAGE_THRESHOLD` pages or fewer  --from-literal=MAX_EMBEDDING_TOKENS=32768

   (default 5) are treated as interactive and served first. Long-waiting large```

   documents are promoted after `AGING_PROMOTION_SECONDS` so they cannot starve.

### 3 — Create the InferenceService

4. **Concurrency budget + vLLM priority.** At most `VLM_MAX_CONCURRENCY`

   (default 4) OCR requests are in flight against the VLM, which runs with```yaml

   `--max-num-seqs=17`; translation therefore always keeps at least 13 slots.apiVersion: serving.kserve.io/v1beta1

   Within the VLM's waiting queue, `--scheduling-policy priority` decideskind: InferenceService

   ordering: translation uses the default `0`, small-document OCR uses `1`, andmetadata:

   large-document OCR uses `5` (lower wins).  name: pdf-ocr-pipeline

spec:

Points 3 and 4 are complementary: the budget limits how much capacity OCR can  predictor:

*hold*, while priority decides the *order* of what is waiting. Neither alone is    containers:

sufficient.      - name: kserve-container

        image: <registry>/<org>/pdf-ocr-pipeline:latest

---        envFrom:

          - secretRef:

## Single-replica constraint              name: pdf-ocr-pipeline-env

```

**This service must run with exactly one replica.**

```bash

The job store, the work queue and the VLM concurrency budget are all held inoc apply -f inferenceservice.yaml

process. A second replica would double the OCR pressure on the shared VLM,```

breaking the translation guarantee, and would route status polls to a pod that

does not know the job.### 4 — Call the service



`manifests/inferenceservice.yaml` pins `minReplicas: maxReplicas: 1` with a```bash

`Recreate` strategy so two pods never run simultaneously, even briefly during acurl -X POST https://<inference-service-url>/v1/models/user-pdf-preprocessing:predict \

rollout.  -H "Content-Type: application/json" \

  -d '{

Scaling out requires an external job store and queue (Redis or equivalent).    "instances": [{

`JobStore` in `jobs/store.py` is already an interface for exactly that; no Redis      "data_url": "<base64-encoded-pdf>",

instance was available in the cluster for this iteration.      "user_uuid": "user-123",

      "doc_title": "My Document"

### Consequences of in-memory state    }]

  }'

- A pod restart loses in-flight jobs. Job ids are prefixed with a per-process```

  instance id, so polling a job from a previous process returns `410 Gone` with

  `code: job_lost` rather than a misleading `404`.The response contains a `documents` array, one entry per chunk, each with `content`, `metadata`, and `embedding` fields.

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

## Not yet implemented

**Phase 4 (efficiency):** text-layer extraction with quality heuristics, and
neighbour-summary contextual retrieval. Both are behind flags, default off.

**Phase 5 (object storage):** dropped; multipart upload is retained.
