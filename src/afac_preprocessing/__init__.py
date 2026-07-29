"""afac_preprocessing — pipeline de prétraitement de documents PDF.

API publique : tout ce qui est listé dans ``__all__`` est stable et destiné
à l'usage bibliothèque ; le reste est interne et peut bouger.

    from afac_preprocessing import Pipeline, PipelineContext, Settings

    settings = Settings.from_dotenv(".env")
    ctx = PipelineContext.for_pdf(Path("doc.pdf"), settings)
    report = Pipeline.default().select(skip=["opencv-check"]).run(ctx)

En ligne de commande : ``afac-preprocess run --input <PDF ou dossier>``.
"""

from .clients.bundle import ClientBundle
from .context import PipelineContext
from .core import (
    PROFILES,
    STEP_REGISTRY,
    BatchReport,
    InProcessRunner,
    Pipeline,
    PipelineReport,
    PipelineStep,
    StepResult,
    StepRunner,
    StepStatus,
    SubprocessRunner,
)
from .exceptions import (
    AfacError,
    ConfigError,
    EmbeddingUnavailable,
    StepFailed,
    StepInputMissing,
    UnknownStep,
    VlmUnavailable,
)
from .settings import Settings
from .workspace import DocumentWorkspace

__all__ = [
    # Orchestration
    "PROFILES",
    "STEP_REGISTRY",
    "BatchReport",
    "Pipeline",
    "PipelineReport",
    "PipelineStep",
    "StepResult",
    "StepStatus",
    # Exécution : contexte, configuration, chemins, clients
    "ClientBundle",
    "DocumentWorkspace",
    "PipelineContext",
    "Settings",
    # Seam in-process / subprocess (lot 7)
    "InProcessRunner",
    "StepRunner",
    "SubprocessRunner",
    # Erreurs métier
    "AfacError",
    "ConfigError",
    "EmbeddingUnavailable",
    "StepFailed",
    "StepInputMissing",
    "UnknownStep",
    "VlmUnavailable",
]
