"""Common contract for the 13 steps: PipelineStep, StepResult, StepStatus.

``execute()`` stays synchronous — it's the common contract, the 7 pure
steps don't need to know async exists. The 6 VLM steps will implement
``async def _execute_async(ctx)`` and delegate via
``return ctx.run_async(self._execute_async(ctx))``.
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
    """Base class for the pipeline steps.

    ``inputs()``/``outputs()`` are declarative: they feed the wiring test
    (bad chaining across the 13 steps becomes impossible to introduce
    silently) and ``steps --graph`` (batch 5). The declarations are
    refined as each step is converted (batch 6).
    """

    name: ClassVar[str]
    description: ClassVar[str] = ""
    requires_vlm: ClassVar[bool] = False
    enabled_by_default: ClassVar[bool] = True  # opencv-check → False

    def inputs(self, ctx: PipelineContext) -> list[Path]:
        """What the step reads (declarative)."""
        return []

    def outputs(self, ctx: PipelineContext) -> list[Path]:
        """What the step writes (declarative)."""
        return []

    def is_applicable(self, ctx: PipelineContext) -> bool:
        """False ⇒ the step is a passthrough for this document."""
        return True

    def validate_inputs(self, ctx: PipelineContext) -> None:
        """Raises StepInputMissing (NOT sys.exit) if a declared input is missing."""
        missing = [p for p in self.inputs(ctx) if not p.exists()]
        if missing:
            raise StepInputMissing(
                f"Step '{self.name}': missing input(s): "
                + ", ".join(str(p) for p in missing)
            )

    @abstractmethod
    def execute(self, ctx: PipelineContext) -> StepResult:
        """The step's work. No os.environ, no argv, no sys.exit, no
        logging.basicConfig, no asyncio.run (invariant #3)."""

    def run(self, ctx: PipelineContext) -> StepResult:
        """Template: applicable? → validate inputs -> execute (timed)."""
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
