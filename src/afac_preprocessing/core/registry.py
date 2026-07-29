"""STEP_REGISTRY — les 13 étapes canoniques du pipeline.

L'ordre canonique vit ici depuis le lot 7 (l'orchestrateur legacy
``pipeline_extraction.py``, dont il était levé au lot 4, est supprimé).
Chaque étape est une classe de ``steps/`` — les déclarations
``inputs``/``outputs`` vivent dans chaque classe ; elles nourrissent le test
de câblage et ``steps --graph``.
"""

from __future__ import annotations

from typing import Callable

from .step import PipelineStep

# Ordre canonique des 13 étapes — hérité de la liste STEPS historique.
STEP_ORDER: tuple[str, ...] = (
    "docling-extract",            # 01 — doctags via Docling
    "reorder-doctags",            # 02 — réordonnancement des balises
    "opencv-check",               # 03 — QA visuelle only (désactivée par défaut)
    "csv-to-jsonlines",           # 04 — CSV → JSONL
    "load-jsonline-doctags",      # 05 — chargement doctags enrichi
    "image-description",          # 06 — descriptions images VLM
    "url-extraction",             # 07 — extraction URL
    "url-tuning",                 # 08 — tuning URL via VLM
    "markdown-convert",           # 09 — conversion markdown
    "markdown-control",           # 10 — contrôle markdown VLM
    "inject-image-descriptions",  # 11 — injection descriptions → _final.md
    "metadata-generation",        # 12 — metadata + embedding CSV
    "hyq-embedding",              # 13 — embeddings des questions hyq
)


def _converted_steps() -> dict[str, Callable[[], PipelineStep]]:
    """Fabriques des 13 classes d'étapes (lot 6 — conversion complète).

    Import paresseux : les modules d'étapes peuvent tirer des dépendances
    lourdes, on ne paie que ce qu'on instancie.
    """
    from ..steps.csv_to_jsonlines import CsvToJsonlinesStep
    from ..steps.docling_extract import DoclingExtractStep
    from ..steps.hyq_embedding import HyqEmbeddingStep
    from ..steps.image_description import ImageDescriptionStep
    from ..steps.inject_image_descriptions import InjectImageDescriptionsStep
    from ..steps.load_jsonline_doctags import LoadJsonlineDoctagsStep
    from ..steps.markdown_control import MarkdownControlStep
    from ..steps.markdown_convert import MarkdownConvertStep
    from ..steps.metadata_generation import MetadataGenerationStep
    from ..steps.opencv_check import OpencvCheckStep
    from ..steps.reorder_doctags import ReorderDoctagsStep
    from ..steps.url_extraction import UrlExtractionStep
    from ..steps.url_tuning import UrlTuningStep

    return {
        "docling-extract": DoclingExtractStep,
        "reorder-doctags": ReorderDoctagsStep,
        "opencv-check": OpencvCheckStep,
        "csv-to-jsonlines": CsvToJsonlinesStep,
        "load-jsonline-doctags": LoadJsonlineDoctagsStep,
        "image-description": ImageDescriptionStep,
        "url-extraction": UrlExtractionStep,
        "url-tuning": UrlTuningStep,
        "markdown-convert": MarkdownConvertStep,
        "markdown-control": MarkdownControlStep,
        "inject-image-descriptions": InjectImageDescriptionsStep,
        "metadata-generation": MetadataGenerationStep,
        "hyq-embedding": HyqEmbeddingStep,
    }


def build_default_steps() -> list[PipelineStep]:
    """Les 13 étapes, dans l'ordre canonique."""
    converted = _converted_steps()
    return [converted[name]() for name in STEP_ORDER]


STEP_REGISTRY: dict[str, PipelineStep] = {step.name: step for step in build_default_steps()}

# Profils nommés (lot 5) — l'exigence « variantes prêtes à l'emploi » (§ 8).
# Une constante, pas de la config à inventer (décision n°15).
PROFILES: dict[str, dict[str, object]] = {
    "full": {"include_disabled": True},                            # les 13 étapes
    "default": {},                                                 # comportement actuel
    "no-images": {"skip": ["image-description"]},                  # le plus demandé
    "no-vlm": {"skip": [n for n, s in STEP_REGISTRY.items() if s.requires_vlm]},
    "extract": {"to": "markdown-convert"},                         # jusqu'au markdown
}
