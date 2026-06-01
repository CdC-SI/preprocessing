import os
import certifi
from pathlib import Path
import logging
from dotenv import load_dotenv

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
_log = logging.getLogger(__name__)

# .env chargement
def load_vlm_config():
    project_root = Path(__file__).resolve().parent.parent
    dotenv_path = project_root / ".env.test" # Choisir l'env à charger selon les besoins
    _log.info(
        "Loading dotenv from: %s (exists: %s)", 
        dotenv_path.resolve(), 
        dotenv_path.exists(),
        )
    load_dotenv(dotenv_path=dotenv_path)

# CA path
    custom_ca = os.environ.get("VLM_CA_PEM")
    ca_path = (
        custom_ca 
        if custom_ca and Path(custom_ca).exists() 
        else certifi.where()
    )

# VLM configuration: URL, model name, 
    vlm_url = os.environ.get("VLM_URL", "")
    vlm_model_name = os.environ.get("VLM_MODEL_NAME", "")
    if not vlm_url:
        raise RuntimeError(
            f"VLM_URL not set. Ensure {dotenv_path} exists and contains VLM_URL."
    )
    
    embedding_url = os.environ.get("EMBEDDING_URL", "")
    embedding_model_name = os.environ.get("EMBEDDING_MODEL_NAME", "")
    if not embedding_url:
        raise RuntimeError(
            f"VLM_URL not set. Ensure {dotenv_path} exists and contains VLM_URL."
    )
    
    reranker_url = os.environ.get("RERANKER_URL", "")
    reranker_model_name = os.environ.get("RERANKER_MOL_NAME", "")
    if not reranker_url:
        raise RuntimeError(
            f"VLM_URL not set. Ensure {dotenv_path} exists and contains VLM_URL."
    )
    
    return {
        "CA_PATH": ca_path,
        "VLM_URL": vlm_url,
        "VLM_MODEL_NAME": vlm_model_name,
        "EMBEDDING_URL": embedding_url,
        "EMBEDDING_MODEL_NAME": embedding_model_name,
        "RERANKER_URL": reranker_url,
        "RERANKER_MODEL_NAME": reranker_model_name,
    }