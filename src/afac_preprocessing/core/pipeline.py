"""Pipeline — registre, sélection, exécution.

La sémantique de ``select()`` est LEVÉE de ``_select_steps`` /
``_resolve_step_ref`` de ``pipeline_extraction.py`` (réutiliser, pas
réécrire) : références par nom ou numéro 1-based, ``only`` prime sur
from/to/skip, exécution toujours dans l'ordre canonique, ``opencv-check``
exclu par défaut (``enabled_by_default=False``) sauf demande explicite.
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
    """Résultat d'un run : durée et statut par étape — remplace le parsing de stdout."""

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
    # CSV globaux reconstruits en fin de batch (lot F2)
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
        """Les 13 étapes, dans l'ordre canonique."""
        return cls(build_default_steps(), runner)

    # --- sélection (sémantique levée de _select_steps / _resolve_step_ref) ---

    def _resolve_ref(self, ref: str | int) -> int:
        """Référence (nom, ou numéro 1-based comme aujourd'hui) → index 0-based."""
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
        """Sous-pipeline, toujours dans l'ordre canonique (méthode pure).

        ``only`` prime sur from/to/skip et inclut même les étapes désactivées
        par défaut (les nommer explicitement vaut opt-in, comme aujourd'hui).
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

    # --- exécution ---

    def run(self, ctx: PipelineContext) -> PipelineReport:
        """Exécute les étapes dans l'ordre ; s'arrête à la première en échec
        (comportement actuel de l'orchestrateur)."""
        report = PipelineReport(doc_name=ctx.workspace.doc_name)
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
        """Un run par document ; un échec isolé n'arrête pas le batch
        (comportement actuel, conservé à l'identique).

        En fin de batch, les CSV globaux par racine sont reconstruits
        (lot F2) — une action d'ensemble, pas une étape par document.
        """
        from ..aggregate import aggregate_all_roots

        batch = BatchReport()
        out_roots: set[Path] = set()
        for ctx in contexts:
            out_roots.add(ctx.settings.output_files_root)
            try:
                report = self.run(ctx)
            except Exception as exc:  # échec isolé ⇒ le batch continue
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
                    # L'agrégation ne doit jamais faire échouer un batch dont
                    # les documents sont traités : elle est rejouable seule
                    # (afac-preprocess aggregate).
                    _log.exception("Agrégation des CSV globaux impossible (%s)", out_root)
        return batch
