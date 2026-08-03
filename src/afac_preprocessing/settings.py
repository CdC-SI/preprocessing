"""Immutable pipeline configuration, loaded once (pydantic-settings).

Replaces utils/config.py, scattered os.environ reads, and the orchestrator's environment mutation. 

The variable names are those from the historical .env file (VLM_URL, VLM_CA_PEM, EMBEDDING_URL, …) 
see .env.example for more details
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import certifi
from pydantic import Field, HttpUrl, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .exceptions import ConfigError


def _find_project_root() -> Path:
    """Go up to the directory containing pyproject.toml (the repository root)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd()


def default_dotenv() -> Path | None:
    """.env then .env.test are searched for in the current directory first, then in the repository root.
    The current directory comes first so that the CLI can still be launched from anywhere with a local .env file,
    the root comes next because standalone scripts (baseline, evaluation, KG) 
    were launched from their own directory and relied on project_root() / ".env.test".
    """
    for base in (Path.cwd(), _find_project_root()):
        for name in (".env", ".env.test"):
            candidate = base / name
            if candidate.exists():
                return candidate
    return None


class Settings(BaseSettings):
    """Run configuration, validated at construction time.
    A malformed URL is an immediate and readable error (ConfigError), not a timeout two minutes later.
    """

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    vlm_url: HttpUrl = Field(validation_alias="VLM_URL")
    vlm_model_name: str = Field(default="", validation_alias="VLM_MODEL_NAME")
    embedding_url: HttpUrl | None = Field(default=None, validation_alias="EMBEDDING_URL")
    embedding_model_name: str = Field(default="", validation_alias="EMBEDDING_MODEL_NAME")
    reranker_url: HttpUrl | None = Field(default=None, validation_alias="RERANKER_URL")
    reranker_model_name: str = Field(default="", validation_alias="RERANKER_MODEL_NAME")
    ca_pem: Path | None = Field(default=None, validation_alias="VLM_CA_PEM")
    enable_image_description: bool = Field(
        default=True, validation_alias="ENABLE_IMAGE_DESCRIPTION"
    )
    # PNG image export by Docling (read by `docling-extract`, 
    # default False, the project's .env sets it to true).
    enable_image_extraction: bool = Field(
        default=False, validation_alias="ENABLE_IMAGE_EXTRACTION"
    )
    vlm_temperature: float = Field(default=0.0, validation_alias="VLM_TEMPERATURE")
    project_root: Path = Field(
        default_factory=_find_project_root, validation_alias="PROJECT_ROOT"
    )
    # data/ lives at the repository root, OUTSIDE src/ (batch 1). Explicit field,
    # overridable via DATA_ROOT (container / CI) without rebuilding.
    data_root: Path = Field(
        default_factory=lambda: _find_project_root() / "data",
        validation_alias="DATA_ROOT",
    )
    # Original .env file, stored by `from_dotenv()`.
    # forwards it to legacy scripts via `--dotenv`, becomes unnecessary once.
    dotenv_path: Path | None = Field(default=None, exclude=True)

    @field_validator(
        "embedding_url", "reranker_url", "ca_pem", mode="before"
    )
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        """The historical .env file uses VAR=\"\" to mean "not provided"""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator(
        "enable_image_description", "enable_image_extraction", "vlm_temperature",
        mode="before",
    )
    @classmethod
    def _blank_to_default(cls, value: Any, info: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            defaults = {
                "enable_image_description": True,
                "enable_image_extraction": False,
                "vlm_temperature": 0.0,
            }
            return defaults[info.field_name]
        return value

    @model_validator(mode="after")
    def _derive_data_root(self) -> Settings:
        """PROJECT_ROOT overridden without DATA_ROOT -> data_root follows project_root."""
        if "data_root" not in self.model_fields_set and "project_root" in self.model_fields_set:
            self.data_root = self.project_root / "data"
        return self

    @property
    def resolved_ca_path(self) -> str:
        """CA path to use: ca_pem if it exists, otherwise certifi."""
        if self.ca_pem is not None and self.ca_pem.exists():
            return str(self.ca_pem)
        return str(certifi.where())

    @property
    def input_files_root(self) -> Path:
        return self.data_root / "input_files"

    @property
    def output_files_root(self) -> Path:
        return self.data_root / "output_files_preprocessing"

    @classmethod
    def from_dotenv(cls, path: Path | None = None) -> Settings:
        """Builds the Settings from a .env file (plus the environment).
        Raises ConfigError (a readable message, not a raw Pydantic traceback) 
        if a required variable is missing or malformed.
        """
        if path is not None and not Path(path).exists():
            raise ConfigError(f".env file not found — {Path(path).resolve()}")
        try:
            settings = cls(_env_file=path)  # type: ignore[call-arg]
            settings.dotenv_path = Path(path).resolve() if path is not None else None
            return settings
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}"
                for err in exc.errors()
            )
            raise ConfigError(
                f"Invalid configuration ({path or 'environment'}): {problems}"
            ) from exc
