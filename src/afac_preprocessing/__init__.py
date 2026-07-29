"""afac_preprocessing — pipeline de prétraitement de documents.

API publique du noyau (lot 2). ``Pipeline`` et le registre d'étapes arrivent
au lot 4 ; la liste ``__all__`` définitive est fixée au lot 8.
"""

from .context import PipelineContext
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
    "AfacError",
    "ConfigError",
    "DocumentWorkspace",
    "EmbeddingUnavailable",
    "PipelineContext",
    "Settings",
    "StepFailed",
    "StepInputMissing",
    "UnknownStep",
    "VlmUnavailable",
]
