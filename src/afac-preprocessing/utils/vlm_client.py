"""
vlm_client.py — Client HTTP partagé pour les appels VLM (API OpenAI-compatible).

Centralise VlmConfig, build_vlm_config, check_vlm_connectivity et call_vlm_async
pour éviter la duplication entre les scripts du pipeline.
"""
import logging
from dataclasses import dataclass

import httpx

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class VlmConfig:
    """Configuration immuable du VLM, construite à partir de l'environnement."""
    ca_path: str
    url: str
    model_name: str


def build_vlm_config() -> VlmConfig:
    """
    Construit la configuration VLM depuis l'environnement.
    Import de utils.config effectué à la demande pour éviter les effets de bord à l'import.

    :return: configuration VLM immuable
    """
    from utils.config import load_vlm_config
    cfg = load_vlm_config()
    return VlmConfig(
        ca_path=cfg["CA_PATH"],
        url=cfg["VLM_URL"],
        model_name=cfg["VLM_MODEL_NAME"],
    )


async def check_vlm_connectivity(client: httpx.AsyncClient, vlm_cfg: VlmConfig) -> bool:
    """
    Vérifie que le VLM est accessible avant de lancer le traitement.

    :param client: client HTTP partagé
    :param vlm_cfg: configuration VLM
    :return: True si accessible, False sinon
    """
    try:
        _log.info("Test de connectivité au VLM : %s ...", vlm_cfg.url)
        payload = {
            "model": vlm_cfg.model_name,
            "messages": [{"role": "user", "content": [{"type": "text", "text": "ping"}]}],
            "max_tokens": 10,
        }
        resp = await client.post(vlm_cfg.url, json=payload, timeout=30)
        resp.raise_for_status()
        _log.info("VLM accessible. HTTP %s", resp.status_code)
        return True
    except Exception as e:
        _log.exception("Impossible de joindre le VLM : %s", e)
        return False


async def call_vlm_async(
    client: httpx.AsyncClient,
    vlm_cfg: VlmConfig,
    image_b64: str,
    prompt: str,
) -> str:
    """
    Envoie une image (page PDF en base64) + un prompt au VLM et retourne la réponse textuelle.

    :param client: client HTTP partagé
    :param vlm_cfg: configuration VLM
    :param image_b64: image encodée en base64
    :param prompt: prompt à envoyer au VLM
    :return: réponse textuelle du VLM
    """
    payload = {
        "model": vlm_cfg.model_name,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                {"type": "text", "text": prompt},
            ],
        }],
        "max_tokens": 8192,
        "temperature": 0.0,
        "chat_template_kwargs": {"enable_thinking": False},  # désactive le mode thinking Qwen3 (évite content=null)
    }
    resp = await client.post(vlm_cfg.url, json=payload)
    resp.raise_for_status()
    data = resp.json()
    message = data["choices"][0]["message"]
    content = message.get("content")
    if content is not None:
        return content.strip()
    _log.warning("content=null, full message: %s", message)
    raise ValueError(f"VLM returned null content. Full message: {message}")
