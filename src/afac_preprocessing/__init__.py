"""afac_preprocessing — PDF document preprocessing pipeline.

Public API: everything listed in __all__ is stable and intended for library use,
everything else is internal and may change.

from afac_preprocessing import Pipeline, PipelineContext, Settings

settings = Settings.from_dotenv(".env")
ctx = PipelineContext.for_pdf(Path("doc.pdf"), settings)
report = Pipeline.default().select(skip=["opencv-check"]).run(ctx)

Command line usage: afac-preprocess run --input <PDF or folder>.
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
    # Execution: context, configuration, paths, clients
    "ClientBundle",
    "DocumentWorkspace",
    "PipelineContext",
    "Settings",
    # Seam in-process / subprocess
    "InProcessRunner",
    "StepRunner",
    "SubprocessRunner",
    # Business errors
    "AfacError",
    "ConfigError",
    "EmbeddingUnavailable",
    "StepFailed",
    "StepInputMissing",
    "UnknownStep",
    "VlmUnavailable",
]
