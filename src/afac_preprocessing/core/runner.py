"""StepRunner, the in-process / subprocess seam.

``docling-extract`` loads torch + easyocr: keeping it in-process across a
long batch is the classic memory-leak scenario. The in-process vs subprocess
decision is made after measuring, but the seam exists from batch 4
onward, otherwise the whole orchestration would need to be reopened to add
it. One-line fallback: put a step back on ``SubprocessRunner``.
"""

from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING, Protocol

from .step import PipelineStep, StepResult, StepStatus

if TYPE_CHECKING:
    from ..context import PipelineContext


class StepRunner(Protocol):
    def run(self, step: PipelineStep, ctx: PipelineContext) -> StepResult: ...


class InProcessRunner:
    """Default: runs the step in the current process.

    NB: as long as a step is a ``ScriptStep`` (batch 4-6), it's the step
    itself that launches a ``python -m`` subprocess, the runner stays
    in-process, the observable behavior remains today's.
    """

    def run(self, step: PipelineStep, ctx: PipelineContext) -> StepResult:
        return step.run(ctx)


class SubprocessRunner:
    """Isolates a step in a child process via the CLI (``run --only <name>``).

    Functional starting from batch 5 (the CLI doesn't exist before that);
    the seam is laid down now so that batch 7 doesn't reopen the orchestration.
    """

    def run(self, step: PipelineStep, ctx: PipelineContext) -> StepResult:
        cmd = [
            sys.executable, "-m", "afac_preprocessing.cli.main",
            "run", "--only", step.name,
            "--input", str(ctx.workspace.source_pdf),
        ]
        if ctx.settings.dotenv_path is not None:
            cmd += ["--dotenv", str(ctx.settings.dotenv_path)]
        completed = subprocess.run(cmd, stdout=sys.stdout, stderr=sys.stderr, text=True)
        if completed.returncode != 0:
            return StepResult(
                StepStatus.FAILED,
                message=f"subprocess exited with code {completed.returncode}",
            )
        return StepResult(StepStatus.OK, outputs=step.outputs(ctx))
