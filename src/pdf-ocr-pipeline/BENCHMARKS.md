# Translation Contention Benchmarks

Tracks the acceptance test for this service's core constraint: **OCR must
never starve the shared translation VLM**. Each entry below is one full
idle/load run pair, captured with `tests/benchmark_contention.py`.

Pass criterion: translation **p95 in the `load` phase within ~20% of the
`idle` phase**, measured back-to-back with matching `--duration`/`--rate` so
sample counts are comparable (translation latency has high natural variance;
small/mismatched samples are not a fair comparison — see the 2026-09-04 entry
below for why).

---

## How to run a measurement round

```bash
source venv_preprocessing/bin/activate   # or your equivalent
cd src/pdf-ocr-pipeline

export NO_PROXY=$NO_PROXY,.zas.admin.ch,.mgnt.zas.admin.ch
export ZIA_TRANSLATION_URL="https://gateway-r.zas.admin.ch/zia-trad/api/translation"
export ZIA_TRANSLATION_TOKEN=$(grep '^BLUE_TOKEN=' .env | cut -d= -f2-)
export PDF_OCR_URL="https://pdf-ocr-pipeline-model-serving.apps.openshift-ai.mgnt.zas.admin.ch"
export AUTH_TOKEN=$(grep '^AUTH_TOKEN=' .env | cut -d= -f2-)

# 1. Load phase FIRST — its actual wall-clock time sets the duration for a
#    fair idle comparison (OCR finishes when it finishes; translation keeps
#    sampling for --duration regardless).
python tests/benchmark_contention.py --phase load --rate 0.3 \
  --pdf tests/fixtures/10-long-pdf/DR-1-45.pdf --duration 900 --out load.json

# 2. Idle phase SECOND, with --duration/--rate matched to how long the load
#    run's translation sampling actually took (see "count" in load.json —
#    at rate 0.3 that's roughly count / 0.3 seconds).
python tests/benchmark_contention.py --phase idle --rate 0.3 \
  --duration <matched_seconds> --out idle.json
```

> Run **load before idle**. The OCR job's duration is unpredictable (depends
> on VLM load from other consumers), so there is no way to pre-size the idle
> run until you know how long the load run's translation sampling actually
> ran for (`count / rate` seconds). Matching sample counts materially changes
> the p95 you get — see below.

To compare against the **old (pre-async) pipeline**, re-run with `--legacy`
against a checkout of `main` (see `RUNBOOK.md` §9i for the rollback
procedure), repeating the same idle/load pair.

To tune, adjust `VLM_MAX_CONCURRENCY` in the `pdf-ocr-pipeline-env` secret
(4 → 3 → 2) and redeploy between rounds if p95 degradation exceeds ~20%.

---

## Results log

### 2026-09-04 — new async pipeline, `VLM_MAX_CONCURRENCY=4`, `model-serving` ns

**OCR job:** `DR-1-45.pdf`, 45 pages, `low` priority — completed in **131.1 s**.

| Run | Duration | Rate | n | min | p50 | p95 | p99 | max | mean |
|---|---|---|---|---|---|---|---|---|---|
| idle (n=5, mismatched) | 15 s | 0.3/s | 5 | 11.52 | 14.73 | **15.44** | 15.44 | 15.44 | 14.22 |
| idle (matched, n=42) | 140 s | 0.3/s | 42 | 12.94 | 23.61 | **31.65** | 33.24 | 33.24 | 23.17 |
| load | ~133 s (OCR-bound) | 0.3/s | 40 | 13.75 | 30.28 | **41.47** | 42.93 | 42.93 | 29.22 |

- **Against the small/mismatched idle sample (n=5):** p95 41.47s vs 15.44s —
  a **+168.6%** apparent degradation. **Discard this comparison** — n=5 at a
  low, bursty rate is not statistically meaningful; it happened to land
  during a lull in the shared translation gateway's own load.
- **Against the matched idle sample (n=42, same duration/rate as the load
  run):** p95 41.47s vs 31.65s — a **+31.0%** degradation. Still outside the
  ~20% acceptance threshold, but far closer than the naive comparison
  suggests, and consistent with real background variance on
  `gateway-r.zas.admin.ch` (its own translation p95 moved from 15s to 32s
  across two idle runs taken minutes apart, before any OCR load existed).
- **Interpretation:** the shared VLM/gateway currently has enough background
  variance that a single idle/load pair is not conclusive proof of pass or
  fail. Treat **+31%** as the current best estimate of OCR's incremental
  contention cost, and prioritise a larger `n` (longer duration, e.g. 5–10
  min per phase) and/or multiple repeated rounds averaged together before
  treating this as a hard gate.
- **Known issue hit during this run:** `GET /jobs/{jobId}/status` on
  `gateway-r.zas.admin.ch` returns a bare `500` (gateway/routing-layer bug,
  not an application bug — the identical logic behind the deprecated
  `GET /pdf/{jobId}/status` alias returns `200` correctly). The benchmark
  script polls the deprecated alias until this is fixed upstream. Flag to
  the zia-translation/gateway owners.
- **Baseline against the OLD (pre-async) pipeline:** not yet captured. The
  old pipeline was already replaced in this namespace before this benchmark
  suite was fixed, so there is no "before" number for the *initial* rollout.
  Next tuning/rollback round should capture both sides in the same sitting
  for a true before/after comparison.

Raw output: `load.json`, `idle.json` (gitignored; re-run to reproduce).
