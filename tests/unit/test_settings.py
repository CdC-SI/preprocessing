"""Tests des Settings — lecture .env, validations, fallbacks."""

from pathlib import Path

import certifi
import pytest

from afac_preprocessing.exceptions import ConfigError
from afac_preprocessing.settings import Settings

_ENV_KEYS = [
    "VLM_URL", "VLM_MODEL_NAME", "EMBEDDING_URL", "EMBEDDING_MODEL_NAME",
    "RERANKER_URL", "RERANKER_MODEL_NAME", "VLM_CA_PEM", "ENABLE_IMAGE_DESCRIPTION",
    "VLM_TEMPERATURE", "PROJECT_ROOT", "DATA_ROOT",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Les tests ne doivent dépendre d'aucun environnement ambiant."""
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _write_env(tmp_path: Path, content: str) -> Path:
    dotenv = tmp_path / ".env"
    dotenv.write_text(content, encoding="utf-8")
    return dotenv


def test_from_dotenv_reads_all_fields(tmp_path: Path) -> None:
    dotenv = _write_env(
        tmp_path,
        'VLM_URL="http://vlm.local/v1/chat/completions"\n'
        'VLM_MODEL_NAME="qwen-vl"\n'
        'EMBEDDING_URL="http://embed.local/v1/embeddings"\n'
        'EMBEDDING_MODEL_NAME="bge-m3"\n'
        'VLM_TEMPERATURE="0.6"\n',
    )
    settings = Settings.from_dotenv(dotenv)
    assert str(settings.vlm_url) == "http://vlm.local/v1/chat/completions"
    assert settings.vlm_model_name == "qwen-vl"
    assert settings.embedding_url is not None
    assert settings.embedding_model_name == "bge-m3"
    assert settings.vlm_temperature == 0.6


def test_missing_vlm_url_raises_config_error(tmp_path: Path) -> None:
    dotenv = _write_env(tmp_path, 'VLM_MODEL_NAME="m"\n')
    with pytest.raises(ConfigError, match="vlm_url|VLM_URL"):
        Settings.from_dotenv(dotenv)


def test_malformed_vlm_url_raises_config_error(tmp_path: Path) -> None:
    dotenv = _write_env(tmp_path, 'VLM_URL="pas-une-url"\n')
    with pytest.raises(ConfigError):
        Settings.from_dotenv(dotenv)


def test_nonexistent_dotenv_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        Settings.from_dotenv(tmp_path / "absent.env")


def test_empty_optional_urls_become_none(tmp_path: Path) -> None:
    # Le .env historique écrit VAR="" pour « non renseigné ».
    dotenv = _write_env(
        tmp_path,
        'VLM_URL="http://vlm.local/v1"\nEMBEDDING_URL=""\nRERANKER_URL=""\nVLM_CA_PEM=""\n',
    )
    settings = Settings.from_dotenv(dotenv)
    assert settings.embedding_url is None
    assert settings.reranker_url is None
    assert settings.ca_pem is None


def test_ca_pem_missing_file_falls_back_to_certifi(tmp_path: Path) -> None:
    dotenv = _write_env(
        tmp_path, f'VLM_URL="http://vlm.local/v1"\nVLM_CA_PEM="{tmp_path}/absent.pem"\n'
    )
    settings = Settings.from_dotenv(dotenv)
    assert settings.resolved_ca_path == certifi.where()


def test_ca_pem_existing_file_is_used(tmp_path: Path) -> None:
    pem = tmp_path / "ca.pem"
    pem.write_text("cert")
    dotenv = _write_env(tmp_path, f'VLM_URL="http://vlm.local/v1"\nVLM_CA_PEM="{pem}"\n')
    settings = Settings.from_dotenv(dotenv)
    assert settings.resolved_ca_path == str(pem)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("true", True), ("True", True), ("1", True), ("False", False), ("false", False),
     ("0", False), ("", True)],  # "" ⇒ défaut (True)
)
def test_enable_image_description_boolean_forms(
    tmp_path: Path, raw: str, expected: bool
) -> None:
    dotenv = _write_env(
        tmp_path, f'VLM_URL="http://vlm.local/v1"\nENABLE_IMAGE_DESCRIPTION="{raw}"\n'
    )
    assert Settings.from_dotenv(dotenv).enable_image_description is expected


def test_data_root_defaults_under_project_root(tmp_path: Path) -> None:
    settings = Settings(
        vlm_url="http://vlm.local/v1",  # type: ignore[arg-type]
        project_root=tmp_path,
    )
    assert settings.data_root == tmp_path / "data"


def test_data_root_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_ROOT", str(tmp_path / "ailleurs"))
    settings = Settings(vlm_url="http://vlm.local/v1")  # type: ignore[arg-type]
    assert settings.data_root == tmp_path / "ailleurs"
    assert settings.input_files_root == tmp_path / "ailleurs" / "input_files"
    assert settings.output_files_root == tmp_path / "ailleurs" / "output_files_preprocessing"


def test_project_root_env_override_drives_data_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))
    settings = Settings(vlm_url="http://vlm.local/v1")  # type: ignore[arg-type]
    assert settings.project_root == tmp_path
    assert settings.data_root == tmp_path / "data"


def test_from_env_without_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VLM_URL", "http://vlm.local/v1/chat/completions")
    settings = Settings.from_dotenv(None)
    assert str(settings.vlm_url).startswith("http://vlm.local")
