"""Contrat commun des 13 étapes : PipelineStep, StepResult, StepStatus.

``execute()`` reste synchrone — c'est le contrat commun, les 7 étapes pures
n'ont pas à savoir que l'async existe. Les 6 étapes VLM implémenteront
``async def _execute_async(ctx)`` et délégueront par
``return ctx.run_async(self._execute_async(ctx))`` (lot 6, contrainte C2 —
jamais ``asyncio.run()``, piège P7).
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from ..exceptions import StepInputMissing

if TYPE_CHECKING:
    from ..context import PipelineContext


class StepStatus(Enum):
    OK = "ok"
    SKIPPED = "skipped"
    PASSTHROUGH = "passthrough"
    FAILED = "failed"


@dataclass
class StepResult:
    status: StepStatus
    outputs: list[Path] = field(default_factory=list)
    duration: float = 0.0
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status is not StepStatus.FAILED


class PipelineStep(ABC):
    """Classe de base des étapes du pipeline.

    ``inputs()``/``outputs()`` sont déclaratifs : ils alimentent le test de
    câblage (le mauvais chaînage des 13 étapes devient impossible à introduire
    silencieusement) et ``steps --graph`` (lot 5). Les déclarations sont
    affinées à la conversion de chaque étape (lot 6).
    """

    name: ClassVar[str]
    description: ClassVar[str] = ""
    requires_vlm: ClassVar[bool] = False
    enabled_by_default: ClassVar[bool] = True  # opencv-check → False

    def inputs(self, ctx: PipelineContext) -> list[Path]:
        """Ce que l'étape lit (déclaratif)."""
        return []

    def outputs(self, ctx: PipelineContext) -> list[Path]:
        """Ce que l'étape écrit (déclaratif)."""
        return []

    def is_applicable(self, ctx: PipelineContext) -> bool:
        """False ⇒ l'étape est un passthrough pour ce document."""
        return True

    def validate_inputs(self, ctx: PipelineContext) -> None:
        """Lève StepInputMissing (PAS sys.exit) si une entrée déclarée manque."""
        missing = [p for p in self.inputs(ctx) if not p.exists()]
        if missing:
            raise StepInputMissing(
                f"Step '{self.name}': missing input(s): "
                + ", ".join(str(p) for p in missing)
            )

    @abstractmethod
    def execute(self, ctx: PipelineContext) -> StepResult:
        """Le travail de l'étape. Ni os.environ, ni argv, ni sys.exit, ni
        logging.basicConfig, ni asyncio.run (invariant n°3)."""

    def run(self, ctx: PipelineContext) -> StepResult:
        """Template : applicable ? → valider les entrées → exécuter (chronométré)."""
        start = time.perf_counter()
        if not self.is_applicable(ctx):
            return StepResult(
                StepStatus.PASSTHROUGH,
                duration=time.perf_counter() - start,
                message="not applicable for this document",
            )
        self.validate_inputs(ctx)
        result = self.execute(ctx)
        result.duration = time.perf_counter() - start
        return result
