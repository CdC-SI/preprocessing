"""Configuration immuable du pipeline, lue une fois (pydantic-settings).

Remplace ``utils/config.py``, les lectures dispersées d'``os.environ`` et la
mutation d'environnement de l'orchestrateur. Règle du refactor : **aucune
lecture d'environnement hors de cette classe.**

Les noms de variables sont ceux du ``.env`` historique (``VLM_URL``,
``VLM_CA_PEM``, ``EMBEDDING_URL``, …) — voir ``.env.example``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import certifi
from pydantic import Field, HttpUrl, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .exceptions import ConfigError


def _find_project_root() -> Path:
    """Remonte jusqu'au dossier contenant pyproject.toml (racine du dépôt)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd()


class Settings(BaseSettings):
    """Configuration d'un run, validée à la construction.

    Une URL malformée est une erreur immédiate et lisible (``ConfigError``),
    pas un timeout deux minutes plus tard.
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
    # Export des PNG d'images par Docling (lu par docling-extract ; défaut
    # False comme le script historique — le .env du projet le met à true).
    enable_image_extraction: bool = Field(
        default=False, validation_alias="ENABLE_IMAGE_EXTRACTION"
    )
    gen_id: str = Field(default="", validation_alias="GEN_ID")
    vlm_temperature: float = Field(default=0.0, validation_alias="VLM_TEMPERATURE")
    project_root: Path = Field(
        default_factory=_find_project_root, validation_alias="PROJECT_ROOT"
    )
    # data/ vit à la racine du dépôt, HORS de src/ (lot 1). Champ explicite,
    # surchargeable par DATA_ROOT (conteneur / CI) sans rebuild.
    data_root: Path = Field(
        default_factory=lambda: _find_project_root() / "data",
        validation_alias="DATA_ROOT",
    )
    # Fichier .env d'origine, mémorisé par from_dotenv(). Les ScriptStep (lot 4)
    # le retransmettent aux scripts legacy via --dotenv ; devient inutile quand
    # toutes les étapes sont converties (lot 6).
    dotenv_path: Path | None = Field(default=None, exclude=True)

    @field_validator(
        "embedding_url", "reranker_url", "ca_pem", mode="before"
    )
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        """Le .env historique écrit VAR=\"\" pour « non renseigné »."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator(
        "enable_image_description", "enable_image_extraction", "vlm_temperature", "gen_id",
        mode="before",
    )
    @classmethod
    def _blank_to_default(cls, value: Any, info: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            defaults = {
                "enable_image_description": True,
                "enable_image_extraction": False,
                "vlm_temperature": 0.0,
                "gen_id": "",
            }
            return defaults[info.field_name]
        return value

    @model_validator(mode="after")
    def _derive_data_root(self) -> "Settings":
        """PROJECT_ROOT surchargé sans DATA_ROOT ⇒ data_root suit project_root."""
        if "data_root" not in self.model_fields_set and "project_root" in self.model_fields_set:
            self.data_root = self.project_root / "data"
        return self

    @property
    def resolved_ca_path(self) -> str:
        """Chemin du CA à utiliser : ``ca_pem`` s'il existe, sinon certifi."""
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
    def from_dotenv(cls, path: Path | None = None) -> "Settings":
        """Construit les Settings depuis un fichier .env (+ environnement).

        Lève ``ConfigError`` (message lisible, pas un traceback pydantic brut)
        si une variable obligatoire manque ou est malformée.
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
