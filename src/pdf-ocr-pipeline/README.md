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

## Generating offline wheels

When the pod has no internet access or no trusted certificates, dependencies are installed from pre-downloaded wheels stored in the model bucket alongside the code. Generate them on a machine that **does** have internet access and the same Python version as the pod image (Python 3.11):

```bash
mkdir -p pdf-ocr-pipeline/wheels
pip download \
  -r pdf-ocr-pipeline/requirements.txt \
  --dest pdf-ocr-pipeline/wheels \
  --platform manylinux2014_x86_64 \
  --python-version 3.11 \
  --only-binary=:all:
```

The `wheels/` folder is then uploaded to S3 alongside the rest of the model files (see [Deploy on OpenShift](#deploy-on-openshift)). The ServingRuntime startup script detects the folder and installs from it:

```bash
if [ -d /mnt/models/wheels ]; then
  pip install --no-index --find-links=/mnt/models/wheels ...
fi
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

### 5 — Required post-deploy patch: `kube-rbac-proxy` upstream timeout

When `security.opendatahub.io/enable-auth: "true"` is set on the
InferenceService (as it is here), the ODH/KServe controller auto-injects a
`kube-rbac-proxy` sidecar in front of `kserve-container` to enforce
SubjectAccessReview auth. This sidecar defaults to `--upstream-timeout=30s`,
which is far too short for this service's synchronous `:predict` endpoint —
a single multi-page document can legitimately take minutes to process. If
left at the default, the sidecar kills the client connection at 30s with a
`502 Bad Gateway`, even though `kserve-container` is healthy and still
working (confirmed via its own trace logs completing the request
successfully well after the client already saw a 502). This is independent
of, and in addition to, the Route's `haproxy.router.openshift.io/timeout`
annotation and the `readinessProbe` settings — both of those were already
correctly configured and are **not** the cause of this failure mode.

**This sidecar is not exposed through `ServingRuntime.spec.containers` or
`InferenceService.spec.predictor` overrides** — it's injected directly by
the controller and any `kube-rbac-proxy` entry added to the ServingRuntime
YAML is silently ignored (verified: `oc apply` succeeds, but the live pod's
container args are unchanged). There is currently no known declarative way
to configure it. Until one is found, it must be patched imperatively on the
Deployment **after every fresh deploy or full pod recreation**:

```bash
oc patch deployment pdf-ocr-pipeline-predictor -n model-serving --type=json \
  -p '[{"op":"add","path":"/spec/template/spec/containers/1/args/-","value":"--upstream-timeout=600s"}]'
```

This appends the flag to the already-injected sidecar's `args` list (index
`1`, i.e. the second container — verify with
`oc get deployment pdf-ocr-pipeline-predictor -n model-serving -o jsonpath='{.spec.template.spec.containers[*].name}'`
if the container order ever changes). The patched Deployment persists
across `oc rollout restart` in RawDeployment mode (it isn't continuously
reconciled from the ServingRuntime), but is lost if the Deployment object
itself is deleted/recreated (e.g. `oc delete inferenceservice` + reapply).

