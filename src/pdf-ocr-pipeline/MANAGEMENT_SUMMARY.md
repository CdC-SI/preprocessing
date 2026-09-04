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
much slower translation gets while OCR is running versus when it's idle.

**Headline result (large, realistic document — 45 pages):**

- **Translation requests were never dropped or blocked.** Every request
  completed successfully throughout the entire OCR run.
- Translation did get **measurably slower** while OCR was running — roughly
  **30% slower** at the high end (the slowest 5% of requests), compared to
  when OCR was idle.
- This is **more slowdown than our internal target** (~20%), so there is
  follow-up work identified: primarily, further reducing how many OCR
  requests are allowed to run at once against the shared GPU, and re-testing.

**For small, everyday documents** (a few pages — the common case for
interactive use), we also compared the new system directly against the old
one on the same infrastructure: **processing time was effectively identical**,
and translation slowdown was minor (roughly 8–11%, within target) for both
the old and new systems. The extra safety machinery does not add overhead for
typical, small documents — its benefit is specifically for large documents,
which the old system could not even protect against without this rebuild
(the old system's synchronous design cannot safely handle large documents at
all, and would either time out or need to reject them outright).

## Bottom line

- **No user-facing translation requests are lost or blocked** by OCR
  activity, even under a realistic large-document load — this was the
  primary risk we set out to eliminate, and it is eliminated.
- **There is a residual, measurable slowdown** (~30% at the tail) for large
  documents that exceeds our internal comfort margin (~20%), so this is
  flagged as an open tuning item rather than a fully closed loop.
- **Everyday, small-document usage is unaffected** — no regression versus
  the previous system.
- The next step is a further reduction of the OCR concurrency limit against
  the shared GPU and re-measuring, to bring the large-document case within
  the same margin already achieved for small documents.

*Last updated: 2026-09-04. See `BENCHMARKS.md` for the full data, methodology,
and an explanation of a gateway-level bug encountered (and worked around)
during testing that is unrelated to this service.*
