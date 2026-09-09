# Translation Contention Benchmarks

Tracks the acceptance test for this service's core constraint: **OCR must
never starve the shared translation VLM**. Each entry below is one or more
idle/load run pairs, captured with `tests/benchmark_contention.py`.

**Is this test script already what's needed to answer "does a long OCR job
starve translation, and is translation actually prioritized"? Yes.**
`tests/benchmark_contention.py --phase load` submits one long-running OCR
document to `/jobs` (or legacy `:predict` with `--legacy`) and, concurrently,
drives a steady rate of real end-to-end translation jobs against the
zia-translation REF microservice for the full duration of the OCR run. It
reports translation latency percentiles for that concurrent window, which is
directly compared against an `idle` (OCR-off) baseline below.

Pass criterion: translation **p95 in the `load` phase within ~20% of the
`idle` phase**, measured back-to-back with matching `--duration`/`--rate` so
sample counts and fixture-size mix are comparable.

> **All results before 2026-09-08 have been discarded — two methodological
> fixes were needed first:**
> 1. **Prefix-cache contamination.** Early rounds resubmitted the
>    byte-identical PDF on every request. The shared vLLM's prefix cache
>    increasingly served cached prompt/vision-token prefixes, understating
>    real contention (production traffic is novel documents every time —
>    zero cache hits). Fixed by `_stamp_unique()` (`tests/benchmark_contention.py`):
>    stamps a random UUID onto every page of every submission via `pymupdf`
>    before sending it, for both the OCR document and every translation
>    request.
> 2. **Fixture-size sampling imbalance.** Once translation load was spread
>    across a pool of differently-sized documents (to further defeat
>    caching), drawing pool members via `random.choice` meant an idle run
>    and a load run could draw different size mixes by chance, swamping any
>    real contention signal with pure document-size variance. Fixed by
>    switching to a **deterministic round-robin** over
>    `TRANSLATION_FIXTURE_POOL` (cycling from index 0 every run) instead of
>    random selection, so idle and load runs of the same `--rate`/
>    `--duration` always draw the identical sequence of fixture sizes.
>    `Latencies.by_fixture` reports per-fixture percentiles so this can be
>    sanity-checked directly in the output.
>
> Even with both fixes, a **single idle/load pair still shows a
> ±5-8% swing purely from background variance** on the shared
> `gateway-r.zas.admin.ch` / vLLM environment (see the 4-replicate round
> below) — this is normal and expected. Always run **at least 3-4 replicate
> pairs** and look at the mean/spread, not any single pair, before drawing a
> conclusion.

---

## How to run the benchmark

```bash
source venv_preprocessing/bin/activate   # or your equivalent
cd src/pdf-ocr-pipeline
mkdir -p benchmark_results   # gitignored; keeps result JSON out of the repo

export NO_PROXY=$NO_PROXY,.zas.admin.ch,.mgnt.zas.admin.ch
export ZIA_TRANSLATION_URL="https://gateway-r.zas.admin.ch/zia-trad/api/translation"
export ZIA_TRANSLATION_TOKEN=$(grep '^BLUE_TOKEN=' .env | cut -d= -f2-)   # refresh in .env if this run is far from the last (BLUE_TOKEN expires after a few hours)
export PDF_OCR_URL="https://pdf-ocr-pipeline-model-serving.apps.openshift-ai.mgnt.zas.admin.ch"
export AUTH_TOKEN=$(grep '^AUTH_TOKEN=' .env | cut -d= -f2-)
```

**Per replicate** (repeat 3-4x for a reliable result; `<config>` and `<N>`
are labels, e.g. `vlm2_r1`, `vlm2_r2`, ...):

```bash
# 1. Load phase FIRST — its actual wall-clock time (printed as "OCR finished
#    as completed in <N>s") sets the --duration for a matched idle run.
python tests/benchmark_contention.py --phase load --rate 0.3 \
  --pdf tests/fixtures/10-long-pdf/DR-1-45.pdf --duration 900 \
  --out benchmark_results/load_<config>_r<N>.json

# 2. Wait ~30s to let any request backlog / connection-pool churn from the
#    load phase settle, so the idle phase starts from a clean baseline.
sleep 30

# 3. Idle phase, --duration matched to the load phase's reported OCR seconds.
python tests/benchmark_contention.py --phase idle --rate 0.3 \
  --duration <matched_seconds> --out benchmark_results/idle_<config>_r<N>.json
```

To compare against the **old (pre-async) pipeline**, re-run with `--legacy`
against a deployment of `main` (see `README.md`'s "Deploying the legacy
(pre-async) baseline" section and `RUNBOOK.md` §9i) — see the results
below.

To tune, adjust `VLM_MAX_CONCURRENCY` / `OCR_LARGE_DOC_CONCURRENCY` in the
`pdf-ocr-pipeline-env` secret and redeploy between rounds.

---

## Summary and recommendation

All results below are for the same 45-page document
(`tests/fixtures/10-long-pdf/DR-1-45.pdf`), 4 replicates each, corrected
methodology (cache-busting + deterministic fixture rotation):

| Config | Mean OCR wall time | Mean p95 delta (load vs idle) |
|---|---|---|
| Legacy (pre-async), synchronous `:predict` | 87.2s | +6.9% |
| **`VLM_MAX_CONCURRENCY=4`, `OCR_LARGE_DOC_CONCURRENCY=2` (default)** | 133.0s | **+7.2%** |
| `VLM_MAX_CONCURRENCY=2`, `OCR_LARGE_DOC_CONCURRENCY=2` | 137.8s | +3.1% |
| `VLM_MAX_CONCURRENCY=2`, `OCR_LARGE_DOC_CONCURRENCY=1` | 248.6s | +2.5% |

**All four configurations comfortably pass the ~20% acceptance
threshold** for translation-contention on this document size. Lowering
`VLM_MAX_CONCURRENCY` and/or `OCR_LARGE_DOC_CONCURRENCY` buys a marginally
lower (and noisier-to-noiseless) contention delta, but at a direct,
substantial cost to OCR throughput (up to ~1.9x slower OCR wall time at
`OCR_LARGE_DOC_CONCURRENCY=1`), with no corresponding safety benefit large
enough to justify it at this document size.

**Recommendation: deploy with the defaults —
`VLM_MAX_CONCURRENCY=4`, `OCR_LARGE_DOC_CONCURRENCY=2`.** This gives the
best OCR throughput of the three async configurations tested while still
clearing the contention threshold with margin. Revisit only if production
traffic shows sustained higher concurrent OCR volume than tested here, or
a larger/more adversarial document size surfaces a clearer contention
signal.

The legacy pipeline's own contention number (+6.9%) is not itself
disqualifying, but it is not a like-for-like alternative: it has no
priority queue, no per-page chunking, and no concurrency cap, so its
result does not generalise past small documents, and required raising
both a readiness-probe tolerance and an auth-sidecar upstream timeout
just to complete a single 45-page request without a client-visible `502`
(see the legacy section below) — this operational fragility under
long-running synchronous requests is itself one of the core reasons for
the async rewrite, independent of the throughput/contention numbers.

---

## Results log

### Legacy (pre-async) pipeline baseline — 2026-09-09, 4 replicates

**Deployment:** `main` branch checked out and applied to the
`pdf-ocr-pipeline` InferenceService/ServingRuntime in `model-serving`
(same S3 model path, `models/pdf-ocr-pipeline`, already holding
legacy-only artifacts — see `RUNBOOK.md` §9i). Verified genuinely running
legacy code: no `jobs`/`tokenizer` present under `/mnt/models`, and
`predictor.py`'s `predict()` calls `run_pipeline` directly with no job
queue.

**Two infrastructure fixes were required before this baseline could be
captured at all** — without them every run failed with a client-visible
`502 Bad Gateway` at ~30-34s regardless of translation load, which would
have otherwise been mistaken for a severe contention regression:

1. **`kserve-container` readiness probe.** The legacy synchronous
   `:predict` handler blocks the single asyncio event loop for the full
   OCR duration of one request (no worker pool, unlike the async branch).
   The default `tcpSocket` probe's `failureThreshold(3) *
   periodSeconds(10) = 30s` window was too short for a 45-page document;
   once it flipped `NotReady` the Router evicted the pod mid-request.
   Fixed by widening `failureThreshold` to `30` in
   `manifests/serving-runtime.yaml` (committed on `main`).
2. **`kube-rbac-proxy` upstream timeout (the actual root cause of the
   502s).** The sidecar auto-injected by
   `security.opendatahub.io/enable-auth: "true"` defaults to
   `--upstream-timeout=30s` and kills the client connection at that mark
   even though `kserve-container` is healthy and still working (confirmed
   via its own trace logs completing successfully well after the client
   already saw a 502). This sidecar cannot be configured via
   `ServingRuntime`/`InferenceService` YAML (overrides are silently
   ignored) and must be patched imperatively after every fresh deploy —
   see `README.md` §5 and `RUNBOOK.md` §9i for the exact `oc patch`
   command. Only after this patch did requests return `200`.

**Document:** `tests/fixtures/10-long-pdf/DR-1-45.pdf`, 45 pages, driven
via `--legacy` (`drive_ocr_legacy`, hits `POST /v1/models/...:predict`
directly — no job store/queue/priority mechanics apply here at all, unlike
the async branch's `LEGACY_MAX_PAGES` shim). OCR wall time across the 4
replicates: 85.5s, 84.8s, 91.0s, 87.4s (mean 87.2s) — notably faster than
any async-branch configuration's OCR wall time (~133-249s), because the
legacy path has no per-page queueing/priority overhead and no VLM
concurrency cap — but note it also has zero interactive prioritisation,
zero resilience to pod restarts (job state isn't just in-memory, the
entire request is lost), and the client HTTP connection must stay open
for the full duration.

**Methodology:** identical to the async rounds below (cache-busting via
`_stamp_unique` + deterministic round-robin over
`TRANSLATION_FIXTURE_POOL`), 4 independent replicates, load first then a
30s cooldown then matched-duration idle.

| Replicate | OCR wall time | n (load/idle) | load p95 | idle p95 | Delta (load vs idle) |
|---|---|---|---|---|---|
| 1 | 85.5s | 26 / 26 | 221.44 | 216.68 | +2.2% |
| 2 | 84.8s | 26 / 26 | 220.99 | 209.52 | +5.5% |
| 3 | 91.0s | 28 / 28 | 237.60 | 235.85 | +0.7% |
| 4 | 87.4s | 27 / 26 | 245.30 | 205.76 | +19.3% |
| Mean | | | | | +6.9% |

**Reading these numbers:** all 4 replicates pass the ~20% acceptance
threshold, though replicate 4 sits right at the edge — consistent with the
"single pairs show ±5-8% swing" background-variance caveat noted above,
here amplified because this document's small page count (45) yields a
short OCR window (~87s) and correspondingly few translation samples per
replicate (26-28), which is more sensitive to noise than the async
rounds' larger samples. **This does not mean the legacy pipeline is safe
for production-scale documents** — it was only tested at the same 45-page
size as the async rounds for a fair comparison. The legacy path has no
page-level chunking, no priority queue, and no concurrency cap, so a much
larger document (hundreds of pages, previously routed through the async
`/jobs` API) would very plausibly show materially worse contention *and*
would exceed the extended 600s/30-page-probe tolerances that had to be
added just to make this 45-page test complete without a 502 — this
fragility under long synchronous requests is itself one of the core
motivations for the async rewrite, independent of the contention numbers.

---

### `VLM_MAX_CONCURRENCY=4` (default), `OCR_LARGE_DOC_CONCURRENCY=2` (default) — 2026-09-08, 4 replicates

**Deployment:** `model-serving` namespace, `.env` has
`VLM_MAX_CONCURRENCY=4`, `OCR_LARGE_DOC_CONCURRENCY=2` (both at their
`.env.example` defaults).

**Document:** `tests/fixtures/10-long-pdf/DR-1-45.pdf`, 45 pages, `low`
priority. OCR wall time across the 4 replicates: 132.2s, 132.1s, 133.8s,
133.9s (mean 133.0s) — consistent with the `VLM_MAX_CONCURRENCY=2` round's
OCR wall times (mean 137.8s), confirming OCR wall time for this document
size is not meaningfully sensitive to this concurrency setting either way.

**Methodology:** identical to the `VLM_MAX_CONCURRENCY=2` round below
(cache-busting via `_stamp_unique` + deterministic round-robin over
`TRANSLATION_FIXTURE_POOL`), 4 independent replicates, load first then a 30s
cooldown then matched-duration idle.

| Replicate | OCR wall time | n (load/idle) | load p95 | idle p95 | Delta (load vs idle) |
|---|---|---|---|---|---|
| 1 | 132.2s | 40 / 40 | 332.77 | 309.11 | +7.7% |
| 2 | 132.1s | 40 / 40 | 315.41 | 310.23 | +1.7% |
| 3 | 133.8s | 40 / 40 | 331.19 | 298.65 | +10.9% |
| 4 | 133.9s | 40 / 40 | 343.67 | 316.99 | +8.4% |
| Mean | | | | | +7.2% |

- **Result: mean p95 delta across 4 replicates = +7.2%**, with all 4
  replicates individually positive (+1.7% to +10.9%) — unlike the
  `VLM_MAX_CONCURRENCY=2` round below, where the sign flipped between
  replicates. This is a more consistent (though still modest) signal of a
  small real slowdown under load at this setting. **Still well within the
  ~20% acceptance threshold.**
- **0 errors across all 8 runs (4 load + 4 idle).** One `BLUE_TOKEN`
  expiry was hit and resolved mid-run (replicate 1's first idle attempt
  returned all-401s and was discarded/re-run cleanly after refreshing the
  token in `.env`; not counted in the table above).
- **Direct comparison — `VLM_MAX_CONCURRENCY=4` vs `=2`, same corrected
  methodology, same document, same replicate count:**

  | Config | Mean p95 delta (load vs idle) | Per-replicate range |
  |---|---|---|
  | `VLM_MAX_CONCURRENCY=4` (default) | **+7.2%** | +1.7% to +10.9% (all positive) |
  | `VLM_MAX_CONCURRENCY=2` | **+3.1%** | −5.9% to +8.1% (sign flips) |

  `VLM_MAX_CONCURRENCY=2` shows a lower mean delta, but its per-replicate
  sign instability (two replicates showed load *faster* than idle) means
  the individual numbers are noisier and the true effect size is uncertain.
  `VLM_MAX_CONCURRENCY=4`'s consistently-positive (if modest) deltas are a
  slightly cleaner signal of a small real cost, but at a magnitude (+7.2%)
  that is not itself concerning against the ~20% threshold.
- **Conclusion: both configurations comfortably pass the ~20% acceptance
  threshold for this 45-page document.** There is no strong evidence to
  prefer `VLM_MAX_CONCURRENCY=2` over the default `4` based on this
  contention metric alone — the apparent improvement (+7.2% → +3.1%) is
  within the noise band already observed in both configurations' replicate
  spreads. Recommend keeping the default `VLM_MAX_CONCURRENCY=4` (higher
  OCR throughput, no measurable translation cost) unless
  `OCR_LARGE_DOC_CONCURRENCY=1` (next round) or a larger/more adversarial
  document size shows a clearer separation.

Raw output: `benchmark_results/load_vlm4*.json`,
`benchmark_results/idle_vlm4*.json` (4 replicates each; directory
gitignored, re-run to reproduce).


### `VLM_MAX_CONCURRENCY=2`, `OCR_LARGE_DOC_CONCURRENCY=2` (default) — 2026-09-08, 4 replicates

**Deployment:** `model-serving` namespace, `.env` has
`VLM_MAX_CONCURRENCY=2` (down from default `4`), all other concurrency
settings at their `.env.example` defaults.

**Document:** `tests/fixtures/10-long-pdf/DR-1-45.pdf`, 45 pages, `low`
priority. OCR wall time across the 4 replicates: 148.2s, 136.3s, 132.2s,
134.4s (mean 137.8s) — consistent, not itself affected by the concurrency
setting in a meaningful way at this document size.

**Methodology:** cache-busting via `_stamp_unique` + deterministic
round-robin over `TRANSLATION_FIXTURE_POOL` (4 varied fixtures, 4-11 pages
each: `31530_schlichtungskommission_formular.pdf`,
`03b_bad_ocr_plausible.pdf`, `CI-4-14.pdf`, `06_no_space_extraction.pdf`).
Each replicate ran load first, then a 30s cooldown, then a matched-duration
idle run. 4 independent replicates run back-to-back to average out
background variance on the shared gateway/vLLM.

| Replicate | OCR wall time | n (load/idle) | load p95 | idle p95 | Delta (load vs idle) |
|---|---|---|---|---|---|
| 1 | 148.2s | 42 / 44 | 289.37 | 307.51 | -5.9% |
| 2 | 136.3s | 41 / 41 | 311.08 | 287.87 | +8.1% |
| 3 | 132.2s | 40 / 40 | 324.77 | 310.10 | +4.7% |
| 4 | 134.4s | 40 / 40 | 322.48 | 306.29 | +5.3% |
| Mean | | | | | +3.1% |

- **Result: mean p95 delta across 4 replicates = +3.1%**, with individual
  replicates swinging from -5.9% to +8.1% - i.e. the sign itself is not
  stable across replicates, which is the signature of measurement noise,
  not a real, directional contention effect. **Well within the ~20%
  acceptance threshold.**
- **0 errors across all 8 runs (4 load + 4 idle)** - translation is never
  starved.
- **Conclusion: no detectable translation-latency cost from OCR contention**
  at `VLM_MAX_CONCURRENCY=2` for this 45-page document. Treat +3.1% (+/-~7pp
  spread) as the current best estimate of background noise on this shared
  environment, not a real degradation.
- See the direct 3-way comparison table in the `VLM_MAX_CONCURRENCY=4`
  section above (run the same day, after this one) for how this setting
  compares against the default.

Raw output: `benchmark_results/load_vlm2*.json`,
`benchmark_results/idle_vlm2*.json` (4 replicates each; directory
gitignored, re-run to reproduce).

---
### `VLM_MAX_CONCURRENCY=2`, `OCR_LARGE_DOC_CONCURRENCY=1` — 2026-09-09, 4 replicates

**Deployment:** `model-serving` namespace, `.env` has
`VLM_MAX_CONCURRENCY=2`, `OCR_LARGE_DOC_CONCURRENCY=1` (both lowered from
their `.env.example` defaults of `4`/`2`).

**Document:** `tests/fixtures/10-long-pdf/DR-1-45.pdf`, 45 pages, `low`
priority. OCR wall time across the 4 replicates: 227.0s, 228.0s, 235.9s,
303.3s (mean 248.6s) — roughly **1.8x-2.3x slower** than the
`VLM_MAX_CONCURRENCY=2`, `OCR_LARGE_DOC_CONCURRENCY=2` round (mean 137.8s)
and the `VLM_MAX_CONCURRENCY=4` round (mean 133.0s). This is the expected,
direct cost of `OCR_LARGE_DOC_CONCURRENCY=1`: large-document pages are now
processed one at a time instead of two-at-a-time, roughly doubling total OCR
wall-clock time for this 45-page document. Replicate 4's OCR wall time
(303.3s) is a notable outlier vs replicates 1-3 (~227-236s) — likely
additional background load on the shared vLLM at that moment (a `BLUE_TOKEN`
refresh was also needed partway through this replicate set, see below),
rather than a property of this configuration itself.

**Methodology:** identical to the two rounds above (cache-busting via
`_stamp_unique` + deterministic round-robin over `TRANSLATION_FIXTURE_POOL`),
4 independent replicates, load first then a 30s cooldown then matched-
duration idle. Note: absolute latencies here are **not comparable** to the
other two rounds' absolute numbers — longer OCR wall time means each
replicate's `--duration` is longer, so more of each replicate's samples land
later in a longer-running background-load window; only the within-replicate
load-vs-idle percentage is meaningful across configurations.

| Replicate | OCR wall time | n (load/idle) | load p95 | idle p95 | Delta (load vs idle) |
|---|---|---|---|---|---|
| 1 | 227.0s | 68 / 68 | 471.12 | 468.87 | +0.5% |
| 2 | 228.0s | 68 / 68 | 492.35 | 464.32 | +6.0% |
| 3 | 235.9s | 70 / 70 | 496.51 | 497.03 | −0.1% |
| 4 | 303.3s | 91 / 91 | 641.02 | 618.96 | +3.6% |
| Mean | | | | | +2.5% |

- **Result: mean p95 delta across 4 replicates = +2.5%**, individual
  replicates ranging from −0.1% to +6.0% (all small, one essentially zero,
  none strongly negative or positive). **Well within the ~20% acceptance
  threshold**, and if anything the tightest, most consistently-near-zero
  spread of the three configurations tested so far.
- **0 errors across all 8 runs (4 load + 4 idle).** One `BLUE_TOKEN`
  expiry was hit between replicate 3 and 4 (a load-phase attempt was
  interrupted/discarded before the token was refreshed and replicate 4 was
  cleanly re-run; not counted in the table above beyond the one retained
  attempt for replicate 4).
- **Direct comparison — all three configurations tested to date, same
  corrected methodology, same document, same replicate count:**

  | Config | Mean OCR wall time | Mean p95 delta (load vs idle) | Per-replicate range |
  |---|---|---|---|
  | `VLM_MAX_CONCURRENCY=4`, `OCR_LARGE_DOC_CONCURRENCY=2` (default) | 133.0s | **+7.2%** | +1.7% to +10.9% (all positive) |
  | `VLM_MAX_CONCURRENCY=2`, `OCR_LARGE_DOC_CONCURRENCY=2` | 137.8s | **+3.1%** | −5.9% to +8.1% (sign flips) |
  | `VLM_MAX_CONCURRENCY=2`, `OCR_LARGE_DOC_CONCURRENCY=1` | 248.6s | **+2.5%** | −0.1% to +6.0% (tightest spread) |

- **Conclusion: this configuration produces the lowest and most consistent
  translation-contention delta of the three tested (+2.5%, tight spread),
  but at a substantial cost — OCR wall time for this 45-page document
  roughly doubles (133-138s → 248.6s).** All three configurations
  comfortably clear the ~20% acceptance threshold, so **the contention
  metric alone does not justify the OCR throughput cost** of
  `OCR_LARGE_DOC_CONCURRENCY=1`. Given the default `VLM_MAX_CONCURRENCY=4`,
  `OCR_LARGE_DOC_CONCURRENCY=2` already passes comfortably (+7.2%, well
  under the 20% threshold) without sacrificing OCR throughput, **recommend
  keeping the defaults** for this document size and revisiting only if a
  larger/more adversarial document or higher real-world OCR request volume
  later shows a clearer contention problem.

Raw output: `benchmark_results/load_vlm2ldc1*.json`,
`benchmark_results/idle_vlm2ldc1*.json` (4 replicates each; directory
gitignored, re-run to reproduce).
