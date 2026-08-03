"""STEP_REGISTRY — the 13 canonical pipeline steps.

The canonical order has lived here since batch 7 (the legacy orchestrator
``pipeline_extraction.py``, from which it was lifted in batch 4, has been
removed). Each step is a class from ``steps/`` — the ``inputs``/``outputs``
declarations live in each class; they feed the wiring test and
``steps --graph``.
"""

from __future__ import annotations

from collections.abc import Callable

from .step import PipelineStep

# Canonical order of the 13 steps — inherited from the historical STEPS list.
STEP_ORDER: tuple[str, ...] = (
    "docling-extract",            # 01 — doctags via Docling
    "reorder-doctags",            # 02 — reordering of tags
    "opencv-check",               # 03 — visual QA only (disabled by default)
    "csv-to-jsonlines",           # 04 — CSV → JSONL
    "load-jsonline-doctags",      # 05 — loading enriched doctags
    "image-description",          # 06 — VLM image descriptions
    "url-extraction",             # 07 — URL extraction
    "url-tuning",                 # 08 — URL tuning via VLM
    "markdown-convert",           # 09 — markdown conversion
    "markdown-control",           # 10 — VLM markdown control
    "inject-image-descriptions",  # 11 — injection of descriptions → _final.md
    "metadata-generation",        # 12 — metadata + embedding CSV
    "hyq-embedding",              # 13 — embeddings of hyq questions
)


def _converted_steps() -> dict[str, Callable[[], PipelineStep]]:
    """Factories for the 13 step classes (batch 6  full conversion).

    Lazy import: step modules can pull in heavy dependencies, we only pay
    for what we instantiate.
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
    """The 13 steps, in canonical order."""
    converted = _converted_steps()
    return [converted[name]() for name in STEP_ORDER]


STEP_REGISTRY: dict[str, PipelineStep] = {step.name: step for step in build_default_steps()}

# Named profiles (batch 5), the "ready-to-use variants" requirement (§ 8).
# A constant, not config to invent (decision #15).
PROFILES: dict[str, dict[str, object]] = {
    "full": {"include_disabled": True},                            # the 13 steps
    "default": {},                                                 # current behavior
    "no-images": {"skip": ["image-description"]},                  # most requested
    "no-vlm": {"skip": [n for n, s in STEP_REGISTRY.items() if s.requires_vlm]},
    "extract": {"to": "markdown-convert"},                         # up to markdown
}
