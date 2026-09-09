# PDF OCR Pipeline — Async Rebuild: Summary for Management

**Audience:** non-technical stakeholders. **Detail level:** what changed, why
it matters, and what the numbers say so far. For engineering detail, see
`README.md` and `BENCHMARKS.md` in this same folder.

---

## The problem we fixed

The document-preprocessing service (OCR) and the translation service share
the same GPU model behind the scenes. Before this change, submitting a large
document for OCR could hold that GPU busy for minutes at a time. Because
translation uses the *same* GPU, a user actively translating a document could
experience slowdowns or timeouts simply because someone else, elsewhere in
the system, had uploaded a large PDF for OCR processing at the same moment.

In short: **a large background job could degrade an interactive, user-facing
feature it has nothing to do with.**

## What changed

The OCR service was rebuilt so that:

1. **Submitting a document no longer blocks.** Instead of waiting for the
   whole document to finish processing, the caller now gets an immediate
   acknowledgement and a reference number, then checks back periodically for
   progress — the same pattern used by the translation service itself.
2. **Large documents are broken into per-page work**, instead of being
   processed as one long, uninterruptible task. This means the system can
   interleave OCR work with other demands on the GPU, rather than
   monopolising it for the full duration of a large document.
3. **Small, interactive documents are prioritised over large ones.** A
   short document (a handful of pages) is treated as urgent and jumps ahead
   of large, bulk documents in the processing queue.
4. **OCR requests are explicitly capped and de-prioritised relative to
   translation** at the level of the shared GPU scheduler itself, so
   translation requests are never queued behind OCR requests.

Together, these four changes are specifically designed so that **translation
stays responsive even while OCR is actively processing a large document in
the background.**

## Does it work? What do the measurements show?

We built a repeatable test that submits a large document for OCR and, at the
same time, continuously sends real translation requests, then measures how
much slower translation gets while OCR is running versus when it's idle. To
get a reliable answer (single test runs on this shared environment show
natural swings of ±5–8% with no OCR running at all), we ran every
configuration **4 times** and looked at the average.

**Headline result (large, realistic document — 45 pages), final tuning
round:**

| Configuration | Translation slowdown while OCR runs |
|---|---|
| **Recommended (default) settings** | **+7.2%** |
| Reduced OCR concurrency (more cautious) | +3.1% |
| Reduced OCR concurrency + reduced large-document parallelism | +2.5%, but OCR itself took **~1.9x longer** |
| Old (pre-rebuild) system, for comparison | +6.9% |

- **Translation requests were never dropped or blocked**, in any
  configuration, across all test runs.
- All configurations comfortably beat our internal target (~20% maximum
  acceptable slowdown) — including the recommended, default settings.
- The more cautious settings shave a further few percentage points off the
  slowdown, but cost real OCR throughput (documents take up to ~1.9x longer
  to process) for a benefit that is within the test's own margin of error,
  not a clear improvement.
- **Decision: keep the default settings.** They give the best OCR
  throughput while already comfortably meeting the translation-protection
  target — there is no evidence the more cautious (and slower) settings are
  worth their cost at current usage levels.

**For small, everyday documents** (a few pages — the common case for
interactive use), we also compared the new system directly against the old
one on the same infrastructure: **processing time was effectively identical**,
and translation slowdown was minor (roughly 8–11%, within target) for both
the old and new systems. The extra safety machinery does not add overhead for
typical, small documents — its benefit is specifically for large documents.

**Comparison against the old (pre-rebuild) system, same 45-page document:**
the old system's slowdown (+6.9%) was, on this specific test, similar to the
new system's default settings. However, this is not an apples-to-apples
alternative: the old system has no safety mechanisms at all (no queue, no
prioritisation, no concurrency limit) — its result reflects testing at the
same size as everything else here, not a demonstration that it would remain
safe on the large, real-world documents (hundreds of pages) the old system
could not reliably handle in production. It also required two infrastructure
workarounds during testing just to complete a single 45-page request without
an outright failure — a fragility that does not exist in the new system, and
is itself part of the case for the rebuild, independent of the numbers above.

## Bottom line

- **No user-facing translation requests are lost or blocked** by OCR
  activity, under any configuration tested — this was the primary risk we
  set out to eliminate, and it is eliminated.
- **Translation slowdown under the recommended (default) settings is +7.2%,
  comfortably within our ~20% target.** This closes out the tuning
  exercise — no further concurrency reduction is recommended at this time.
- **Everyday, small-document usage is unaffected** — no regression versus
  the previous system.
- **Recommended production configuration:** `VLM_MAX_CONCURRENCY=4`,
  `OCR_LARGE_DOC_CONCURRENCY=2` (the defaults) — best OCR throughput of the
  configurations tested, while already meeting the translation-protection
  target with margin.
- No further action is planned unless production traffic patterns change
  materially (e.g. sustained higher concurrent OCR volume, or much larger
  documents than tested here), in which case this benchmark suite can be
  re-run against the new traffic profile.

*Last updated: 2026-09-09 — final round, including the old-system baseline
and the concurrency-tuning comparison. See `BENCHMARKS.md` for the full
data, methodology, and engineering detail.*
