"""ScriptStep — adaptateur script legacy → étape du registre.

Reproduit à l'identique le comportement de ``_run_step`` de
``pipeline_extraction.py`` tel qu'il est après le lot 1 :
``[sys.executable, "-m", module, "--dotenv", ...]`` — **jamais un chemin de
fichier** (invariant n°7, pièges P1/P2). Différence assumée : au lieu de
muter ``os.environ`` (``_set_doc_env``), l'environnement du fils reçoit une
copie enrichie de DOC_NAME/DOC_PATH — même effet observable pour le script,
zéro état global dans le parent.

Chaque ScriptStep disparaît au lot 6, remplacé par une vraie classe.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Sequence

from ..exceptions import ConfigError
from .step import PipelineStep, StepResult, StepStatus

if TYPE_CHECKING:
    from ..context import PipelineContext

PathsFn = Callable[["PipelineContext"], list[Path]]


class ScriptStep(PipelineStep):
    """Enveloppe un module d'étape existant, exécuté par ``python -m``."""

    def __init__(
        self,
        *,
        name: str,
        module: str,
        description: str = "",
        requires_vlm: bool = False,
        enabled_by_default: bool = True,
        inputs_fn: PathsFn | None = None,
        outputs_fn: PathsFn | None = None,
        extra_args: Sequence[str] = (),
    ) -> None:
        self.name = name
        self.module = module
        self.description = description
        self.requires_vlm = requires_vlm
        self.enabled_by_default = enabled_by_default
        self._inputs_fn = inputs_fn
        self._outputs_fn = outputs_fn
        self.extra_args = tuple(extra_args)

    def inputs(self, ctx: "PipelineContext") -> list[Path]:
        return self._inputs_fn(ctx) if self._inputs_fn else []

    def outputs(self, ctx: "PipelineContext") -> list[Path]:
        return self._outputs_fn(ctx) if self._outputs_fn else []

    def validate_inputs(self, ctx: "PipelineContext") -> None:
        """No-op : le script legacy fait ses propres vérifications — on ne
        change pas son comportement observable avant sa conversion (lot 6)."""

    def _child_env(self, ctx: "PipelineContext") -> dict[str, str]:
        """DOC_NAME/DOC_PATH pour le fils — même logique que ``_set_doc_env``."""
        env = dict(os.environ)
        env["DOC_NAME"] = ctx.workspace.doc_name
        source = ctx.workspace.source_pdf.resolve()
        input_root = ctx.settings.input_files_root.resolve()
        try:
            env["DOC_PATH"] = str(source.relative_to(input_root))
        except ValueError:
            env["DOC_PATH"] = str(source)
        return env

    def execute(self, ctx: "PipelineContext") -> StepResult:
        dotenv = ctx.settings.dotenv_path
        if dotenv is None:
            raise ConfigError(
                f"Step '{self.name}' is a legacy script and needs a .env file: "
                "build Settings with Settings.from_dotenv(path)."
            )
        cmd = [
            sys.executable, "-m", self.module,
            "--dotenv", str(dotenv),
            *self.extra_args,
        ]
        if ctx.dry_run:
            return StepResult(StepStatus.SKIPPED, message=f"dry-run: {' '.join(cmd)}")
        completed = subprocess.run(
            cmd,
            stdout=sys.stdout,
            stderr=sys.stderr,
            text=True,
            env=self._child_env(ctx),
        )
        if completed.returncode != 0:
            return StepResult(
                StepStatus.FAILED,
                message=f"{self.name} ({self.module.rsplit('.', 1)[-1]}) "
                f"exited with code {completed.returncode}",
            )
        return StepResult(StepStatus.OK, outputs=self.outputs(ctx))
