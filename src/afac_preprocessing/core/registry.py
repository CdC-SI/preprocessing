"""STEP_REGISTRY — the 14 canonical pipeline steps.

The canonical order has lived here since batch 7 (the legacy orchestrator
``pipeline_extraction.py``, from which it was lifted in batch 4, has been
removed). Each step is a class from ``steps/`` — the ``inputs``/``outputs``
declarations live in each class; they feed the wiring test and
``steps --graph``.
"""

from __future__ import annotations

from collections.abc import Callable

from .step import PipelineStep

# Canonical order of the 14 steps — inherited from the historical STEPS list.
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
    "table-jsonl-normalize",      # 12 — residual Markdown tables → JSONL → _final_embed.md
    "metadata-generation",        # 13 — metadata + embedding CSV
    "hyq-embedding",              # 14 — embeddings of hyq questions
)


def _converted_steps() -> dict[str, Callable[[], PipelineStep]]:
    """Factories for the 14 step classes (batch 6  full conversion).

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
    from ..steps.table_jsonl_normalize import TableJsonlNormalizeStep
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
        "table-jsonl-normalize": TableJsonlNormalizeStep,
        "metadata-generation": MetadataGenerationStep,
        "hyq-embedding": HyqEmbeddingStep,
    }


def _fixed_table_steps() -> dict[str, Callable[[], PipelineStep]]:
    """Opt-in variants of steps 04/05 (``--fixed-tables``).

    They fix a silent table *substitution* — see
    ``steps/table_injection_fixed.py``. Kept out of the canonical mapping so
    that a run without the flag behaves exactly as before.
    """
    from ..steps.table_injection_fixed import (
        FixedCsvToJsonlinesStep,
        FixedLoadJsonlineDoctagsStep,
    )

    return {
        "csv-to-jsonlines": FixedCsvToJsonlinesStep,
        "load-jsonline-doctags": FixedLoadJsonlineDoctagsStep,
    }


def _reorder_v3_step() -> dict[str, Callable[[], PipelineStep]]:
    """Opt-in variant of step 02 (``--reorder-v3``).

    Sources page boundaries from Docling's own JSON (``prov[].page_no``,
    verified reliable on 134/134 documents) instead of reconstructing them by
    counting ``<page_footer>``/``<page_break>`` in the flat ``.doctags``
    text, then sorts each correctly-bounded page by (y0, x0) like the
    canonical step — see ``steps/reorder_doctags_v3.py``.
    """
    from ..steps.reorder_doctags_v3 import ReorderDoctagsV3Step

    return {"reorder-doctags": ReorderDoctagsV3Step}


def _deterministic_url_tuning_step() -> dict[str, Callable[[], PipelineStep]]:
    """Opt-in variant of step 08 (``--deterministic-urls``).

    Injects hyperlinks already extracted by url-extraction (step 07) via
    anchor-text search, no VLM call, no image render — see
    ``steps/url_tuning_fixed.py``.
    """
    from ..steps.url_tuning_fixed import DeterministicUrlTuningStep

    return {"url-tuning": DeterministicUrlTuningStep}


def build_default_steps(
    *, fixed_tables: bool = False, reorder_v3: bool = False, deterministic_urls: bool = False
) -> list[PipelineStep]:
    """The 14 steps, in canonical order.

    Each flag swaps one or two steps for an opt-in variant. Every substitute
    subclasses the canonical step and keeps its name, ``inputs()`` and
    ``outputs()``, so selection and wiring are unaffected — combining flags
    is safe, each only touches its own step(s):

    - ``fixed_tables``: steps 04/05, no silent table substitution.
    - ``reorder_v3``: step 02, page boundaries from the JSON instead of the
      flat doctags text.
    - ``deterministic_urls``: step 08, no VLM call.
    """
    converted = _converted_steps()
    if fixed_tables:
        converted.update(_fixed_table_steps())
    if reorder_v3:
        converted.update(_reorder_v3_step())
    if deterministic_urls:
        converted.update(_deterministic_url_tuning_step())
    return [converted[name]() for name in STEP_ORDER]


STEP_REGISTRY: dict[str, PipelineStep] = {step.name: step for step in build_default_steps()}

# Named profiles (batch 5), the "ready-to-use variants" requirement (§ 8).
# A constant, not config to invent (decision #15).
PROFILES: dict[str, dict[str, object]] = {
    "full": {"include_disabled": True},                            # the 14 steps
    "default": {},                                                 # current behavior
    "no-images": {"skip": ["image-description"]},                  # most requested
    "no-vlm": {"skip": [n for n, s in STEP_REGISTRY.items() if s.requires_vlm]},
    "extract": {"to": "markdown-convert"},                         # up to markdown
}
