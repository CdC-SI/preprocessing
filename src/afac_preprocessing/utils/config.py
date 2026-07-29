"""⚠ COUCHE DE COMPAT (lot 3 du refactor) — façade au-dessus de ``Settings``.

``load_vlm_config()`` garde sa signature et son dict de retour historiques ;
la validation (VLM_URL) et la politique CA (VLM_CA_PEM → certifi) sont
déléguées au noyau. L'effet de bord ``load_dotenv`` (chargement du .env dans
``os.environ``) est conservé : les scripts d'étape lisent encore DOC_NAME /
DOC_PATH depuis l'environnement jusqu'au lot 6.

La configuration globale du logging au niveau module (effet de bord à
l'import) a été supprimée — c'est la CLI qui configurera le logging (lot 5).
"""
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from ..exceptions import ConfigError
from ..settings import Settings

_log = logging.getLogger(__name__)


def load_vlm_config(dotenv_path: Path | None = None):
    from .paths import project_root

    resolved_path = Path(dotenv_path).resolve() if dotenv_path else project_root() / ".env.test"
    if resolved_path.exists():
        load_dotenv(dotenv_path=resolved_path)
        _log.info("Environment loaded from: %s", resolved_path)
    else:
        _log.debug(".env file missing (%s) — variables read from the environment.", resolved_path)

    try:
        # L'environnement vient d'être peuplé par load_dotenv : le noyau lit env-only.
        settings = Settings.from_dotenv(None)
    except ConfigError as exc:
        raise RuntimeError(
            f"VLM_URL not set. Ensure {resolved_path} exists and contains VLM_URL."
        ) from exc

    # CA path — VLM_CA_PEM or certifi fallback (politique portée par Settings)
    ca_path = settings.resolved_ca_path
    if settings.ca_pem is not None and str(ca_path) == str(settings.ca_pem):
        _log.info("CA used: %s (VLM_CA_PEM)", ca_path)
    else:
        _log.info("CA used: %s (certifi fallback)", ca_path)

    # Valeurs brutes de l'environnement, comme avant le refactor (pas de
    # normalisation d'URL pydantic dans le dict de compat).
    return {
        "CA_PATH": ca_path,
        "VLM_URL": os.environ.get("VLM_URL", ""),
        "VLM_MODEL_NAME": os.environ.get("VLM_MODEL_NAME", ""),
        "EMBEDDING_URL": os.environ.get("EMBEDDING_URL", ""),
        "EMBEDDING_MODEL_NAME": os.environ.get("EMBEDDING_MODEL_NAME", ""),
        "RERANKER_URL": os.environ.get("RERANKER_URL", ""),
        "RERANKER_MODEL_NAME": os.environ.get("RERANKER_MODEL_NAME", ""),
        "GEN_ID": os.environ.get("GEN_ID", ""),
        "DOC_NAME": os.environ.get("DOC_NAME", ""),
    }
