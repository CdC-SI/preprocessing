"""
vlm_client.py — Client OpenAI unifié pour tous les appels VLM/embedding du pipeline.

Avant ce module, chaque script VLM construisait son propre client HTTP (httpx sync, httpx
async, requests, ou openai) avec sa propre logique de retry :
  - description_image_context.py : requests, aucun retry
  - url_tuning_vlm.py             : httpx.AsyncClient, retry maison (3 tentatives, 15s*n)
  - markdown_control_vlm.py       : httpx.AsyncClient (via l'ancien utils/vlm_client.py),
                                             retry maison (_should_retry, _MAX_RETRIES, _RETRY_DELAYS)
  - enhancement_metadata.py       : openai.OpenAI, aucun retry explicite
  - embedding_metadata.py /
    hyq_embedding_doc.py          : openai.OpenAI, generate_embedding dupliqué

Ce module remplace tout ça par une paire de clients openai (sync + async), en s'appuyant sur
le retry intégré du SDK (max_retries=3 : connexion, 408/409/429, 5xx, avec backoff) plutôt que
sur des boucles de retry réécrites à chaque script.

Pas de cache disque ici (délibéré) : chaque appel touche réellement le VLM/l'endpoint
d'embedding à chaque exécution.
"""
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar
from urllib.parse import urlparse, urlunparse

import httpx
from openai import AsyncOpenAI, OpenAI
from pydantic import BaseModel

_log = logging.getLogger(__name__)

_T = TypeVar("_T", bound=BaseModel)

DEFAULT_VISION_MAX_TOKENS = 8192
DEFAULT_MAX_RETRIES = 3
DEFAULT_TIMEOUT = 120.0
_ENABLE_THINKING_FALSE = {"chat_template_kwargs": {"enable_thinking": False}}  # évite content=null (Qwen3)


# Configuration
@dataclass(frozen=True)
class VlmConfig:
    """Configuration immuable, construite depuis l'environnement (VLM_URL, EMBEDDING_URL, ...)."""
    ca_path: str
    vlm_base_url: str
    vlm_model_name: str
    embedding_base_url: str
    embedding_model_name: str


def _to_base_url(raw_url: str) -> str:
    """Réduit une URL d'endpoint complète (ex. .../v1/chat/completions) à scheme://host/v1,
    format attendu par le SDK OpenAI en base_url (il ajoute lui-même /chat/completions,
    /embeddings, /models, ...). Même logique que enhancement_metadata.py /
    embedding_metadata.py avant ce correctif — centralisée ici."""
    if not raw_url:
        return ""
    parsed = urlparse(raw_url)
    return urlunparse((parsed.scheme, parsed.netloc, "/v1", "", "", ""))


def build_vlm_config(dotenv_path: Path | None = None) -> VlmConfig:
    """Construit la configuration depuis l'environnement (import à la demande, cf. utils.config)."""
    from utils.config import load_vlm_config
    cfg = load_vlm_config(dotenv_path=dotenv_path)
    return VlmConfig(
        ca_path=cfg["CA_PATH"],
        vlm_base_url=_to_base_url(cfg["VLM_URL"]),
        vlm_model_name=cfg["VLM_MODEL_NAME"],
        embedding_base_url=_to_base_url(cfg["EMBEDDING_URL"]),
        embedding_model_name=cfg["EMBEDDING_MODEL_NAME"],
    )


# Construction des clients
def build_sync_client(
    cfg: VlmConfig, *, timeout: float = DEFAULT_TIMEOUT, max_retries: int = DEFAULT_MAX_RETRIES
) -> OpenAI:
    """Client sync pour les appels vision/texte (chat.completions)."""
    return OpenAI(
        base_url=cfg.vlm_base_url,
        api_key="no-key",
        http_client=httpx.Client(verify=cfg.ca_path),
        timeout=timeout,
        max_retries=max_retries,
    )


def build_async_client(
    cfg: VlmConfig, *, timeout: float = DEFAULT_TIMEOUT, max_retries: int = DEFAULT_MAX_RETRIES
) -> AsyncOpenAI:
    """Client async pour les appels vision/texte concurrents (url_tuning, markdown_control)."""
    return AsyncOpenAI(
        base_url=cfg.vlm_base_url,
        api_key="no-key",
        http_client=httpx.AsyncClient(verify=cfg.ca_path),
        timeout=timeout,
        max_retries=max_retries,
    )


def build_embedding_client(
    cfg: VlmConfig, *, timeout: float = DEFAULT_TIMEOUT, max_retries: int = DEFAULT_MAX_RETRIES
) -> OpenAI:
    """Client sync pour les embeddings — host distinct du VLM chat (EMBEDDING_URL != VLM_URL)."""
    return OpenAI(
        base_url=cfg.embedding_base_url,
        api_key="no-key",
        http_client=httpx.Client(verify=cfg.ca_path),
        timeout=timeout,
        max_retries=max_retries,
    )


# Connectivité
def check_vlm_connectivity(client: OpenAI, model_name: str) -> bool:
    """Vérifie que le VLM est joignable ET que le modèle configuré est bien servi (GET
    /v1/models) avant un traitement long. Un gateway OpenAI-compatible peut répondre à
    /v1/models même si le backend qui sert model_name est down — on échoue donc
    explicitement si model_name est absent de la liste, plutôt que de logger un simple
    warning et continuer (le pipeline échouerait de toute façon plus tard, mais de façon
    diffuse, page par page, au lieu d'un arrêt net et immédiat)."""
    try:
        available = [m.id for m in client.models.list().data]
        _log.info("VLM OK — modèles disponibles : %s", available or ["(aucun)"])
        if model_name and model_name not in available:
            _log.error("Modèle configuré '%s' absent de la liste VLM : %s", model_name, available)
            return False
        return True
    except Exception:
        _log.exception("VLM non joignable")
        return False


async def check_vlm_connectivity_async(client: AsyncOpenAI, model_name: str) -> bool:
    """Version async — voir check_vlm_connectivity()."""
    try:
        resp = await client.models.list()
        available = [m.id for m in resp.data]
        _log.info("VLM OK — modèles disponibles : %s", available or ["(aucun)"])
        if model_name and model_name not in available:
            _log.error("Modèle configuré '%s' absent de la liste VLM : %s", model_name, available)
            return False
        return True
    except Exception:
        _log.exception("VLM non joignable")
        return False


# Appels haut niveau (vision, texte structuré, embedding) — le SDK gère les retries transitoires
def vision_completion(
    client: OpenAI,
    model: str,
    prompt: str,
    image_b64: str,
    *,
    max_tokens: int = DEFAULT_VISION_MAX_TOKENS,
    temperature: float = 0.0,
) -> str:
    """Envoie image + prompt, retourne le texte. Lève ValueError si le VLM renvoie content=null."""
    response = client.chat.completions.create(
        model=model,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
            ],
        }],
        max_tokens=max_tokens,
        temperature=temperature,
        extra_body=_ENABLE_THINKING_FALSE,
    )
    content = response.choices[0].message.content
    if content is None:
        raise ValueError(f"VLM a renvoyé content=null. Réponse complète : {response}")
    return content.strip()


async def vision_completion_async(
    client: AsyncOpenAI,
    model: str,
    prompt: str,
    image_b64: str,
    *,
    max_tokens: int = DEFAULT_VISION_MAX_TOKENS,
    temperature: float = 0.0,
) -> str:
    response = await client.chat.completions.create(
        model=model,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
            ],
        }],
        max_tokens=max_tokens,
        temperature=temperature,
        extra_body=_ENABLE_THINKING_FALSE,
    )
    content = response.choices[0].message.content
    if content is None:
        raise ValueError(f"VLM a renvoyé content=null. Réponse complète : {response}")
    return content.strip()


def text_completion(
    client: OpenAI,
    model: str,
    system_prompt: str,
    user_content: str,
    *,
    temperature: float = 0.0,
) -> str:
    """Complétion texte libre, sans contrainte de schéma (few-shot prompting)."""
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        temperature=temperature,
        extra_body=_ENABLE_THINKING_FALSE,
    )
    content = response.choices[0].message.content
    if content is None:
        raise ValueError(f"VLM a renvoyé content=null. Réponse complète : {response}")
    return content.strip()


def text_completion_structured(
    client: OpenAI,
    model: str,
    system_prompt: str,
    user_content: str,
    response_format: type[_T],
) -> _T:
    """Structured output (Pydantic) — resume/intent/hyq (enhancement_metadata.py)."""
    response = client.beta.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        response_format=response_format,
        extra_body=_ENABLE_THINKING_FALSE,
    )
    return response.choices[0].message.parsed


def get_embedding(client: OpenAI, model: str, text: str) -> list[float]:
    response = client.embeddings.create(input=text, model=model)
    return response.data[0].embedding


def embedding_to_string(embedding: list[float]) -> str:
    """Ex: [0.4, 0.8, 1.5] -> \"0.4, 0.8, 1.5\" (format colonne EMBEDDING des CSV)."""
    return str(embedding).replace("[", "").replace("]", "")