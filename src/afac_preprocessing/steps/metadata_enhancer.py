"""MetadataEnhancer — enrichissement VLM des métadonnées (resume, intent, hyq).

Collaborateur de l'étape metadata-generation (piège P4 : ce n'est PAS une
étape du registre). Conversion de ``metadata/enhancement_metadata.py``
(vague D) : fonctions et prompts DÉPLACÉS tels quels ; les appels passent en
async via ``vlm.text_completion_structured`` (la fonction écrite au lot 2,
piège P6) — le client vient du ClientBundle, jamais construit ici.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel

from ..prompts.metadata_prompts import (
    HYQ_PROMPT,
    INTENT_PROMPT_1,
    INTENT_PROMPT_2,
    INTENT_PROMPT_3,
    RESUME_PROMPT,
)

if TYPE_CHECKING:
    from ..clients.base import AsyncVlmClient
    from ..workspace import DocumentWorkspace

_log = logging.getLogger(__name__)


# Pydantic models pour le response_format des appels VLM

class ResumeOutput(BaseModel):
    resume: str


class IntentOutput(BaseModel):
    intent: list[str]


class HyQOutput(BaseModel):
    hyq: list[str]


def read_final_markdown(workspace: DocumentWorkspace) -> str:
    """Markdown final du document (équivalent de _read_markdown : _final.md)."""
    if workspace.final_markdown.exists():
        return workspace.final_markdown.read_text(encoding="utf-8")
    return ""


def write_enrichment_output(
    workspace: DocumentWorkspace,
    resume: str,
    intent: list[str],
    hyq: list[str],
) -> Path:
    """
    Écrit les 3 fichiers d'enrichissement dans metadata/ (mêmes noms et mêmes
    formats que le script historique) :
        metadata/resume.md   - résumé en texte markdown
        metadata/intent.json - liste d'intents (array JSON)
        metadata/hyq.json    - liste de questions hypothétiques (array JSON)
    """
    out_dir = workspace.metadata_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    workspace.resume_markdown.write_text(resume, encoding="utf-8")
    workspace.intent_json.write_text(
        json.dumps(intent, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    workspace.hyq_json.write_text(
        json.dumps(hyq, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return out_dir


class MetadataEnhancer:
    """Les 3 générations VLM de l'enrichissement — mêmes prompts, contrat async."""

    def __init__(self, vlm: AsyncVlmClient) -> None:
        self._vlm = vlm

    async def generate_resume(self, markdown_content: str) -> str:
        """Génère un résumé court du document markdown via structured output."""
        result = await self._vlm.text_completion_structured(
            RESUME_PROMPT, markdown_content, ResumeOutput
        )
        return result.resume

    async def generate_intent(self, markdown_content: str) -> list[str]:
        """Génère une liste d'intents/objectifs du document depuis 3 perspectives
        expertes. Les 3 appels sont fusionnés et dédupliqués (même logique que le
        script — séquentiels, dans le même ordre de prompts)."""
        intents: list[str] = []
        seen: set[str] = set()
        for system_prompt in [INTENT_PROMPT_1, INTENT_PROMPT_2, INTENT_PROMPT_3]:
            result = await self._vlm.text_completion_structured(
                system_prompt, markdown_content, IntentOutput
            )
            for item in result.intent:
                normalized = item.strip()
                if normalized and normalized not in seen:
                    seen.add(normalized)
                    intents.append(normalized)
        return intents

    async def generate_hyq(self, markdown_content: str) -> list[str]:
        """Génère une liste de questions hypothétiques auxquelles le document
        peut répondre."""
        result = await self._vlm.text_completion_structured(
            HYQ_PROMPT, markdown_content, HyQOutput
        )
        return result.hyq

    async def run(self, workspace: DocumentWorkspace) -> dict:
        """Équivalent de run_enhancement : lit le markdown final, appelle les 3
        générations VLM et écrit resume.md / intent.json / hyq.json."""
        markdown_content = read_final_markdown(workspace)
        if not markdown_content:
            raise FileNotFoundError(
                f"Aucun fichier markdown trouvé pour '{workspace.doc_name}' "
                f"dans {workspace.root.parent}"
            )

        _log.info("Création du résumé")
        resume = await self.generate_resume(markdown_content)

        _log.info("Création des 'intents'")
        intent = await self.generate_intent(markdown_content)

        _log.info("Création des questions hypothétiques (hyq)")
        hyq = await self.generate_hyq(markdown_content)

        out_dir = write_enrichment_output(workspace, resume, intent, hyq)
        _log.info("Sortie écrite dans : %s", out_dir)

        return {"resume": resume, "intent": intent, "hyq": hyq}
