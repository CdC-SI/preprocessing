# Translation Contention Benchmarks

Tracks the acceptance test for this service's core constraint: **OCR must
never starve the shared translation VLM**. Each entry below is one full
idle/load run pair, captured with `tests/benchmark_contention.py`.

**Is this test script already what's needed to answer "does a long OCR job
starve translation, and is translation actually prioritized"? Yes.**
`tests/benchmark_contention.py --phase load` submits one long-running OCR
document to `/jobs` (or legacy `:predict` with `--legacy`) and, concurrently,
drives a steady rate of real end-to-end translation jobs against the
zia-translation REF microservice for the full duration of the OCR run. It
reports translation latency percentiles for that concurrent window, which is
directly compared against an `idle` (OCR-off) baseline below. **Result from
round 1** (45-page document, the realistic "long OCR task" case): translation
requests are **not starved** — every single one still completed successfully
(0 errors across both phases) — but they are **not fully isolated from OCR
either**: p95 latency rose from 31.65s (idle) to 41.47s (load), a **+31%**
slowdown, moderately over the ~20% target. This is consistent with the
design: OCR is sent to the shared VLM with lower vLLM request priority and a
hard concurrency cap (`VLM_MAX_CONCURRENCY=4` of `--max-num-seqs=17`), so
translation is protected from being blocked or timed out, but a long OCR job
still measurably competes for the same GPU/scheduler resources. See
round 1's full entry below for the numbers and caveats, and `README.md` →
"How it protects translation" for the four mechanisms behind this behaviour.

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
mkdir -p benchmark_results   # gitignored; keeps result JSON out of the repo

export NO_PROXY=$NO_PROXY,.zas.admin.ch,.mgnt.zas.admin.ch
export ZIA_TRANSLATION_URL="https://gateway-r.zas.admin.ch/zia-trad/api/translation"
export ZIA_TRANSLATION_TOKEN=$(grep '^BLUE_TOKEN=' .env | cut -d= -f2-)
export PDF_OCR_URL="https://pdf-ocr-pipeline-model-serving.apps.openshift-ai.mgnt.zas.admin.ch"
export AUTH_TOKEN=$(grep '^AUTH_TOKEN=' .env | cut -d= -f2-)

# 1. Load phase FIRST — its actual wall-clock time sets the duration for a
#    fair idle comparison (OCR finishes when it finishes; translation keeps
#    sampling for --duration regardless).
python tests/benchmark_contention.py --phase load --rate 0.3 \
  --pdf tests/fixtures/10-long-pdf/DR-1-45.pdf --duration 900 \
  --out benchmark_results/load.json

# 2. Idle phase SECOND, with --duration/--rate matched to how long the load
#    run's translation sampling actually took (see "count" in load.json —
#    at rate 0.3 that's roughly count / 0.3 seconds).
python tests/benchmark_contention.py --phase idle --rate 0.3 \
  --duration <matched_seconds> --out benchmark_results/idle.json
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

Raw output: `benchmark_results/load.json`, `benchmark_results/idle.json`
(directory gitignored; re-run to reproduce).

---

### 2026-09-04 (round 2) — legacy `:predict` vs async `/jobs`, same deployment, small doc

**Goal:** isolate the endpoint's own contention cost from OCR document size,
by hitting **both** the legacy synchronous `:predict` route and the new async
`/jobs` route on the *same* running deployment with the *same* document.
This is **not** a before/after comparison of the old pre-async pipeline vs
the new one (that requires a `main` checkout redeploy — still outstanding,
see round 1 above); it isolates whether the async/job-queue machinery itself
adds material overhead versus the legacy code path, for a document small
enough that both can process it (legacy rejects anything over
`LEGACY_MAX_PAGES=10` with `413 use_async_api`).

**Document:** `tests/fixtures/4-mixed-digital-scanned/fixture04.pdf`, 7 pages
(mixed digital/scanned, exercises real VLM OCR — the largest fixture that
still fits under the legacy 10-page cap).

| Run | Duration | Rate | n | OCR wall time | min | p50 | p95 | p99 | max | mean |
|---|---|---|---|---|---|---|---|---|---|---|---|
| idle (matched) | 27 s | 0.3/s | 9 | — | 13.75 | 16.86 | **17.88** | 17.88 | 17.88 | 16.65 |
| load, legacy `:predict` | ~25 s (OCR-bound) | 0.3/s | 8 | 24.9 s | 13.52 | 15.02 | **19.34** | 19.34 | 19.34 | 15.47 |
| load, async `/jobs` | ~25 s (OCR-bound) | 0.3/s | 8 | 25.0 s | 15.57 | 18.14 | **19.87** | 19.87 | 19.87 | 17.53 |

- **OCR wall time is essentially identical** (24.9s legacy vs 25.0s async) for
  the same 7-page document — the async job-queue/worker-pool machinery adds
  no measurable processing overhead by itself.
- **Translation contention, both within the ~20% acceptance threshold** for a
  document this small:
  - Legacy: p95 17.88s → 19.34s = **+8.2%**
  - Async: p95 17.88s → 19.87s = **+11.1%**
  - The ~3pp gap between them is within noise at n=8 and not a meaningful
    regression — both endpoints behave equivalently at this scale.
- **Caveat — this does not contradict round 1's +31% finding.** A 7-page
  document is exactly the kind of small/interactive job the priority queue
  and page-level work-unit design are meant to make cheap; the whole point of
  the async redesign is to protect translation on *large* documents (round
  1's 45-page case), which the legacy endpoint cannot even accept. The two
  rounds measure different things: round 1 = async's cost under a large,
  legacy-incompatible document; round 2 = async vs legacy cost parity on a
  small, legacy-compatible document. Both are needed for the full picture.
- **Still outstanding:** a true old-pipeline-vs-new-pipeline round using the
  *same* 45-page document, requiring a `git checkout main` redeploy per
  `RUNBOOK.md` §9i, since the legacy endpoint on the current deployment
  cannot process documents that large at all (`413 use_async_api`).

Raw output: `benchmark_results/load_legacy_7p.json`,
`benchmark_results/load_async_7p.json`, `benchmark_results/idle_7p_matched.json`
(directory gitignored; re-run to reproduce).
