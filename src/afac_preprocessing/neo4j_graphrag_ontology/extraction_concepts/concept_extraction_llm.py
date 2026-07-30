"""
concept_extraction_llm.py — Extraction libre de concepts métier via Qwen, thinking activé
(ticket Jira 548 : "guidage et extraction libre par notre modèle Qwen AVEC thinking activé").

Sortie volontairement simple ({"concepts": [...]}) — pas d'entités typées ni de relations
(cf. graphrag/extraction_vlm_*.py pour ça), on ne veut ici que la liste de notions métier.

Extraction volontairement DÉCONNECTÉE de ontology/afac_ontology.py : cette ontologie a été
construite à partir de ce même corpus de 20 documents et porte des limites déjà connues
(variantes de casse non couvertes, cf. mémoire projet) — s'en servir pour guider ou
normaliser CETTE extraction serait circulaire (l'extraction est censée nourrir l'ontologie,
pas l'inverse, cf. le même principe déjà énoncé dans extraction_schema.py : "on observe le
vocabulaire spontané du modèle... matière première pour affiner l'ontologie plus tard").

Choix délibéré : PAS de sortie structurée (client.beta.chat.completions.parse). C'est
exactement la combinaison (structured output + thinking) que le commentaire
_ENABLE_THINKING_FALSE dans utils/vlm_client.py signale comme risquée (content=null connu
sur Qwen3). JSON libre + text_completion_thinking + parsing tolérant (extract_json_object,
déjà utilisé par extraction_vlm_fewshot.py) est la combinaison la plus sûre.

Usage :
    uv run --active python neo4j_graphrag_ontology/extraction_concepts/concept_extraction_llm.py --doc-name Mineur
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from ...clients.openai_client import build_async_client, text_completion_thinking_async  # noqa: E402
from ...settings import (
    Settings,  # noqa: E402
    )
from ..shared.extraction_vlm_common import (  # noqa: E402
    DEFAULT_OUTPUT_DIR,
    DOMAIN_CONTEXT,
    DocumentLocator,
    TextChunker,
    TolerantJsonParser,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
_log = logging.getLogger("concept_extraction_llm")

# Définition volontairement écrite ici, en clair — PAS tirée de ontology/afac_ontology.py
# (cf. docstring du module ci-dessus).
_CONCEPT_DEFINITION = (
    "un statut, un objet ou un terme spécialisé du domaine de l'assurance facultative "
    "(ex. mineur, majeur, adhésion, date d'effet, NAVS, R+F)."
)


class ConceptLLMExtractor:
    """Extrait la liste des concepts métier d'un document via Qwen (thinking activé).

    Instanciation : `ConceptLLMExtractor(dotenv)`. `await extract(doc_name, output_dir)`
    par document — construit le client une seule fois, réutilisé pour tous les appels.
    Async depuis le lot 9 : tous les appels VLM du dépôt le sont (exigence métier).
    """

    def __init__(self, dotenv: str | None = None) -> None:
        settings = Settings.from_dotenv(Path(dotenv) if dotenv else None)
        self.model_name = settings.vlm_model_name
        self._client = build_async_client(settings)
        self._chunker = TextChunker()
        self._json_parser = TolerantJsonParser()

    @staticmethod
    def _build_system_prompt() -> str:
        return f"""Tu extrais les concepts métier mentionnés dans un document. {DOMAIN_CONTEXT}

Un concept est une notion métier du domaine : {_CONCEPT_DEFINITION}

Réfléchis avant de répondre : repère les statuts, objets et termes spécialisés du texte, \
sans te limiter aux mots isolés — un concept peut être une expression courte (ex. « date \
d'effet », « 5 ans d'assurance préalable »).

Retourne un objet JSON de la forme : {{"concepts": ["...", "..."]}}
N'entoure pas le JSON de backticks. Ne renvoie que le JSON, rien d'autre.
"""

    async def extract(self, doc_name: str, output_dir: Path = DEFAULT_OUTPUT_DIR) -> list[str]:
        """Concepts bruts (non normalisés, non dédupliqués au-delà des chunks) pour un doc."""
        text = DocumentLocator(output_dir).resolve_final_md(doc_name).read_text(encoding="utf-8")
        system_prompt = self._build_system_prompt()
        chunks = self._chunker.split(text)
        _log.info("[%s] %d caractères, %d chunk(s) à envoyer au VLM (%s)", doc_name, len(text), len(chunks), self.model_name)

        all_concepts: list[str] = []
        for i, chunk in enumerate(chunks, 1):
            _log.info("[%s] chunk %d/%d — appel VLM (thinking activé)...", doc_name, i, len(chunks))
            raw = await text_completion_thinking_async(
                self._client, self.model_name, system_prompt, chunk
            )
            parsed = self._json_parser.parse(raw)
            chunk_concepts = [c.strip() for c in parsed.get("concepts", []) if c and c.strip()]
            _log.info("[%s] chunk %d/%d — %d concepts bruts", doc_name, i, len(chunks), len(chunk_concepts))
            all_concepts += chunk_concepts

        seen: dict[str, str] = {}
        for c in all_concepts:
            key = c.lower()
            if key not in seen:
                seen[key] = c
        _log.info("[%s] %d concepts uniques après dédoublonnage inter-chunks", doc_name, len(seen))
        return list(seen.values())


async def main() -> None:
    ap = argparse.ArgumentParser(description="Extraction libre de concepts métier via Qwen (thinking activé) sur un document AFAC.")
    ap.add_argument("--doc-name", default="Mineur")
    ap.add_argument("--dotenv", default=".env.test")
    ap.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    args = ap.parse_args()

    extractor = ConceptLLMExtractor(args.dotenv)
    concepts = await extractor.extract(args.doc_name, Path(args.output_dir))

    print(f"\n=== Concepts LLM ({extractor.model_name}) — {args.doc_name} ===")
    print(f"{len(concepts)} concepts")
    for c in concepts:
        print(f"  - {c}")


if __name__ == "__main__":
    asyncio.run(main())
