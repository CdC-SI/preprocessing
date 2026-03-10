# PDF OCR Pipeline

A [KServe](https://kserve.github.io/website/) inference service that converts PDF documents into chunked, embedded documents ready for ingestion into a RAG vector database.

## How it works

1. **Render** — the PDF is decoded and each page is rasterised to a PNG image.
2. **OCR** — pages are sent concurrently (up to 4 at a time) to a Vision-Language Model (VLM) which extracts text, tables, and figures as Markdown.
3. **Chunk** — OCR'd pages are accumulated into chunks that stay within the embedding model's token limit, using a tokenizer endpoint for accurate counting.
4. **Summarise** — a single LLM call over the full text produces a document-level language tag and 2-3 sentence summary used to contextualise every chunk.
5. **Embed** — all chunks are embedded in a single batch call to the embedding model.
6. **Return** — each chunk is returned as a document object containing content, metadata (title, language, summary, pages, …), and a pre-formatted embedding vector.

## Environment variables

Copy `.env.example` to `.env` and fill in your endpoint URLs and model names.

| Variable | Description |
|---|---|
| `VLM_URL` | OpenAI-compatible base URL for the Vision-Language Model |
| `VLM_MODEL_NAME` | Model name for the VLM |
| `LLM_URL` | OpenAI-compatible base URL for the Language Model |
| `LLM_MODEL_NAME` | Model name for the LLM |
| `EMBEDDING_URL` | OpenAI-compatible base URL for the embedding model |
| `EMBEDDING_MODEL_NAME` | Model name for the embedding model |
| `TOKENIZE_URL` | Full URL of the vLLM `/tokenize` endpoint |
| `TOKENIZE_MODEL_NAME` | Model name for the tokenizer |
| `MAX_EMBEDDING_TOKENS` | Maximum tokens per chunk (default: `32768`) |

## Local development

```bash
pip install -r requirements.txt
cp .env.example .env  # fill in your values
python predictor.py
```

## Deploy on OpenShift

### Prerequisites

- OpenShift cluster with the **KServe** (or OpenShift AI / RHOAI) operator installed.
- A container image built from this repo pushed to an accessible registry.

### 1 — Build and push the image

```bash
podman build -t <registry>/<org>/pdf-ocr-pipeline:latest .
podman push <registry>/<org>/pdf-ocr-pipeline:latest
```

### 2 — Create a Secret with environment variables

```bash
oc create secret generic pdf-ocr-pipeline-env \
  --from-literal=VLM_URL=https://your-vlm-endpoint/v1 \
  --from-literal=VLM_MODEL_NAME=your-vlm-model \
  --from-literal=LLM_URL=https://your-llm-endpoint/v1 \
  --from-literal=LLM_MODEL_NAME=your-llm-model \
  --from-literal=EMBEDDING_URL=https://your-embedding-endpoint/v1 \
  --from-literal=EMBEDDING_MODEL_NAME=your-embedding-model \
  --from-literal=TOKENIZE_URL=https://your-tokenizer-endpoint/tokenize \
  --from-literal=TOKENIZE_MODEL_NAME=your-tokenizer-model \
  --from-literal=MAX_EMBEDDING_TOKENS=32768
```

### 3 — Create the InferenceService

```yaml
apiVersion: serving.kserve.io/v1beta1
kind: InferenceService
metadata:
  name: pdf-ocr-pipeline
spec:
  predictor:
    containers:
      - name: kserve-container
        image: <registry>/<org>/pdf-ocr-pipeline:latest
        envFrom:
          - secretRef:
              name: pdf-ocr-pipeline-env
```

```bash
oc apply -f inferenceservice.yaml
```

### 4 — Call the service

```bash
curl -X POST https://<inference-service-url>/v1/models/user-pdf-preprocessing:predict \
  -H "Content-Type: application/json" \
  -d '{
    "instances": [{
      "data_url": "<base64-encoded-pdf>",
      "user_uuid": "user-123",
      "doc_title": "My Document"
    }]
  }'
```

The response contains a `documents` array, one entry per chunk, each with `content`, `metadata`, and `embedding` fields.
