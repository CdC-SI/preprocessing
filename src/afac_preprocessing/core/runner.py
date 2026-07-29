"""StepRunner — le seam in-process / subprocess.

``docling-extract`` charge torch + easyocr : le garder en process dans un long
batch est le scénario de fuite mémoire classique. La décision in-process vs
subprocess se prend après mesure (lot 7) — mais le seam existe dès le lot 4,
sinon il faudrait rouvrir toute l'orchestration pour l'ajouter. Repli à une
ligne : remettre une étape sur ``SubprocessRunner``.
"""

from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING, Protocol

from .step import PipelineStep, StepResult, StepStatus

if TYPE_CHECKING:
    from ..context import PipelineContext


class StepRunner(Protocol):
    def run(self, step: PipelineStep, ctx: "PipelineContext") -> StepResult: ...


class InProcessRunner:
    """Défaut : exécute l'étape dans le process courant.

    NB : tant qu'une étape est un ``ScriptStep`` (lot 4-6), c'est l'étape
    elle-même qui lance un subprocess ``python -m`` — le runner reste
    in-process, le comportement observable reste celui d'aujourd'hui.
    """

    def run(self, step: PipelineStep, ctx: "PipelineContext") -> StepResult:
        return step.run(ctx)


class SubprocessRunner:
    """Isole une étape dans un process fils via la CLI (``run --only <name>``).

    Fonctionnel à partir du lot 5 (la CLI n'existe pas avant) ; le seam est
    posé dès maintenant pour que le lot 7 ne rouvre pas l'orchestration.
    """

    def run(self, step: PipelineStep, ctx: "PipelineContext") -> StepResult:
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
