"""Pipeline, registry, selection, execution.

The semantics of ``select()`` are LIFTED from ``_select_steps`` /
``_resolve_step_ref`` in ``pipeline_extraction.py`` (reuse, not rewrite):
references by name or 1-based number, ``only`` takes precedence over
from/to/skip, execution always in canonical order, ``opencv-check``
excluded by default (``enabled_by_default=False``) unless explicitly
requested.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ..exceptions import AfacError, UnknownStep
from .registry import build_default_steps
from .runner import InProcessRunner, StepRunner
from .step import PipelineStep, StepResult, StepStatus

if TYPE_CHECKING:
    from ..context import PipelineContext

_log = logging.getLogger(__name__)


@dataclass
class PipelineReport:
    """Result of a run: duration and status per step — replaces stdout parsing."""

    doc_name: str
    results: list[tuple[str, StepResult]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(result.ok for _, result in self.results)

    @property
    def duration(self) -> float:
        return sum(result.duration for _, result in self.results)


@dataclass
class BatchReport:
    reports: list[PipelineReport] = field(default_factory=list)
    # Global CSVs rebuilt at the end of the batch (batch F2)
    aggregated: list[Path] = field(default_factory=list)

    @property
    def failed(self) -> list[PipelineReport]:
        return [report for report in self.reports if not report.ok]

    @property
    def ok(self) -> bool:
        return not self.failed


class Pipeline:
    def __init__(
        self,
        steps: Sequence[PipelineStep],
        runner: StepRunner | None = None,
    ) -> None:
        self.steps = list(steps)
        self.runner: StepRunner = runner if runner is not None else InProcessRunner()

    @classmethod
    def default(cls, runner: StepRunner | None = None) -> Pipeline:
        """The 13 steps, in canonical order."""
        return cls(build_default_steps(), runner)

    # --- selection (semantics lifted from _select_steps / _resolve_step_ref) ---

    def _resolve_ref(self, ref: str | int) -> int:
        """Reference (name, or 1-based number as today) -> 0-based index."""
        names = [step.name for step in self.steps]
        text = str(ref).strip()
        if text in names:
            return names.index(text)
        if text.isdigit() and 1 <= int(text) <= len(self.steps):
            return int(text) - 1
        valid = ", ".join(f"{i}={name}" for i, name in enumerate(names, start=1))
        raise UnknownStep(f"Unknown step {text!r}. Valid steps: {valid}")

    def select(
        self,
        *,
        from_: str | int | None = None,
        to: str | int | None = None,
        skip: Iterable[str | int] = (),
        only: Iterable[str | int] = (),
        include_disabled: bool = False,
    ) -> Pipeline:
        """Sub-pipeline, always in canonical order (pure method).

        ``only`` takes precedence over from/to/skip and includes even steps
        disabled by default (naming them explicitly counts as opt-in, as today).
        """
        only_list = list(only)
        if only_list:
            indices = sorted({self._resolve_ref(ref) for ref in only_list})
            return Pipeline([self.steps[i] for i in indices], self.runner)

        skip_indices = {self._resolve_ref(ref) for ref in skip}
        start = self._resolve_ref(from_) if from_ is not None else 0
        end = self._resolve_ref(to) if to is not None else len(self.steps) - 1

        selected = [
            step
            for i, step in enumerate(self.steps)
            if start <= i <= end
            and i not in skip_indices
            and (include_disabled or step.enabled_by_default)
        ]
        return Pipeline(selected, self.runner)

    # --- execution ---

    def run(self, ctx: PipelineContext) -> PipelineReport:
        """Executes the steps in order; stops at the first failure
        (current behavior of the orchestrator).

        ``ctx.dry_run`` short-circuits execution: steps are reported as
        SKIPPED, none is actually run, no file is written. The flag has
        existed since batch 5 but was never read anywhere — a ``--dry-run``
        used to run the full pipeline, VLM calls included.
        """
        report = PipelineReport(doc_name=ctx.workspace.doc_name)
        if ctx.dry_run:
            for step in self.steps:
                report.results.append(
                    (step.name, StepResult(StepStatus.SKIPPED, message="dry-run"))
                )
            return report
        for step in self.steps:
            start = time.perf_counter()
            try:
                result = self.runner.run(step, ctx)
            except AfacError as exc:
                result = StepResult(
                    StepStatus.FAILED,
                    duration=time.perf_counter() - start,
                    message=str(exc),
                )
            report.results.append((step.name, result))
            if not result.ok:
                _log.error("Step '%s' failed: %s", step.name, result.message)
                break
        return report

    def run_batch(
        self, contexts: Iterable[PipelineContext], *, aggregate: bool = True
    ) -> BatchReport:
        """One run per document; an isolated failure does not stop the batch
        (current behavior, kept identical).

        At the end of the batch, the global CSVs per root are rebuilt
        (batch F2), an aggregate action, not a per-document step.
        """
        from ..aggregate import aggregate_all_roots

        batch = BatchReport()
        out_roots: set[Path] = set()
        for ctx in contexts:
            out_roots.add(ctx.settings.output_files_root)
            try:
                report = self.run(ctx)
            except Exception as exc:  # isolated failure -> the batch continues
                report = PipelineReport(doc_name=ctx.workspace.doc_name)
                report.results.append(
                    ("(pipeline)", StepResult(StepStatus.FAILED, message=str(exc)))
                )
                _log.exception("Document '%s' failed", ctx.workspace.doc_name)
            batch.reports.append(report)

        if aggregate:
            for out_root in sorted(out_roots):
                try:
                    batch.aggregated += aggregate_all_roots(out_root)
                except OSError:
                    # Aggregation must never fail a batch whose documents
                    # were processed successfully: it can be replayed on its
                    # own (afac-preprocess aggregate).
                    _log.exception("Global CSV aggregation failed (%s)", out_root)
        return batch
