"""Pipeline business error hierarchy.

The core raises these exceptions, never sys.exit or SystemExit
Only the CLI (cli/main.py) and the compatibility layer utils/ translate them into exit codes.
"""

from __future__ import annotations


class AfacError(Exception):
    """Common base class for all pipeline business errors."""


class ConfigError(AfacError):
    """Invalid or incomplete configuration (`.env`, variables, paths)."""


class StepInputMissing(AfacError):
    """An input declared by a step is missing from the workspace."""


class StepFailed(AfacError):
    """A step failed during execution."""


class VlmUnavailable(AfacError):
    """The VLM client is required but unavailable (missing URL or unreachable)."""


class EmbeddingUnavailable(AfacError):
    """The embedding client is required but unavailable."""


class UnknownStep(AfacError):
    """Step name unknown to the registry."""
