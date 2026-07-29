import os
import certifi
from pathlib import Path
import logging
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
_log = logging.getLogger(__name__)


def load_vlm_config(dotenv_path: Path | None = None):
    from .paths import project_root as _project_root

    project_root = _project_root()
    resolved_path = Path(dotenv_path).resolve() if dotenv_path else project_root / ".env.test"
    if resolved_path.exists():
        load_dotenv(dotenv_path=resolved_path)
        _log.info("Environment loaded from: %s", resolved_path)
    else:
        _log.debug(".env file missing (%s) — variables read from the environment.", resolved_path)

    # CA path — VLM_CA_PEM or certifi fallback
    custom_ca = os.environ.get("VLM_CA_PEM")
    if custom_ca and Path(custom_ca).exists():
        ca_path = custom_ca
        _log.info("CA used: %s (VLM_CA_PEM)", ca_path)
    else:
        ca_path = certifi.where()
        _log.info("CA used: %s (certifi fallback)", ca_path)

    vlm_url = os.environ.get("VLM_URL", "")
    if not vlm_url:
        raise RuntimeError(
            f"VLM_URL not set. Ensure {resolved_path} exists and contains VLM_URL."
        )

    return {
        "CA_PATH": ca_path,
        "VLM_URL": vlm_url,
        "VLM_MODEL_NAME": os.environ.get("VLM_MODEL_NAME", ""),
        "EMBEDDING_URL": os.environ.get("EMBEDDING_URL", ""),
        "EMBEDDING_MODEL_NAME": os.environ.get("EMBEDDING_MODEL_NAME", ""),
        "RERANKER_URL": os.environ.get("RERANKER_URL", ""),
        "RERANKER_MODEL_NAME": os.environ.get("RERANKER_MODEL_NAME", ""),
        "GEN_ID": os.environ.get("GEN_ID", ""),
        "DOC_NAME": os.environ.get("DOC_NAME", ""),
    }
