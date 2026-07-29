"""STEP_REGISTRY — les 13 étapes canoniques, levées depuis la liste ``STEPS``
de ``pipeline_extraction.py`` (l'actif à réutiliser, § 1.3 du plan — on ne
réinvente pas la liste).

Au lot 4, chaque entrée est un ``ScriptStep`` ; le lot 6 les remplace une à
une par de vraies classes, sans toucher au reste.

Les déclarations ``inputs``/``outputs`` reflètent le chaînage réel observé
sur la sortie disque ; elles sont affinées à la conversion de chaque étape
(lot 6). Elles nourrissent le test de câblage et ``steps --graph``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable

from ..pipeline_preprocessing.orchestrators.pipeline_extraction import STEPS as _LEGACY_STEPS
from .script_step import ScriptStep
from .step import PipelineStep

if TYPE_CHECKING:
    from ..context import PipelineContext

_VLM_STEPS = frozenset(
    {"image-description", "url-tuning", "markdown-control", "metadata-generation", "hyq-embedding"}
)

_DESCRIPTIONS = {
    "docling-extract": "Extraction Docling : doctags, JSON, texte, tables, images",
    "reorder-doctags": "Réordonnancement des balises doctags",
    "opencv-check": "QA visuelle OpenCV (aucune sortie consommée en aval)",
    "csv-to-jsonlines": "Conversion des tables CSV en JSONL",
    "load-jsonline-doctags": "Doctags enrichis des tables (et images)",
    "image-description": "Descriptions d'images via VLM",
    "url-extraction": "Extraction des hyperliens du PDF",
    "url-tuning": "Correction des URLs dans les doctags via VLM",
    "markdown-convert": "Conversion doctags → markdown",
    "markdown-control": "Contrôle qualité du markdown via VLM",
    "inject-image-descriptions": "Injection des descriptions d'images → _final.md",
    "metadata-generation": "Metadata + embedding → CSV final",
    "hyq-embedding": "Embeddings des questions hypothétiques (hyq)",
}

_W = Callable[["PipelineContext"], list[Path]]


def _io(name: str) -> tuple[_W, _W]:
    """(inputs_fn, outputs_fn) des étapes encore en ScriptStep — chaînage
    relevé sur le disque. Les étapes converties déclarent leur I/O dans leur
    classe ; leurs entrées disparaissent d'ici à la conversion (lot 6)."""
    ws = lambda ctx: ctx.workspace  # noqa: E731

    table: dict[str, tuple[_W, _W]] = {
        "docling-extract": (
            lambda c: [ws(c).source_pdf],
            lambda c: [ws(c).doctags, ws(c).docling_json, ws(c).text_dump,
                       ws(c).markdown, ws(c).tables_dir, ws(c).used_images_dir],
        ),
        "image-description": (
            lambda c: [ws(c).source_pdf, ws(c).reordered_with_tables_doctags,
                       ws(c).used_images_dir],
            lambda c: [ws(c).image_descriptions,
                       ws(c).reordered_with_tables_pictures_doctags],
        ),
        "url-tuning": (
            lambda c: [ws(c).reordered_with_tables_pictures_doctags, ws(c).hyperlinks_jsonl],
            lambda c: [ws(c).url_vlm_doctags],
        ),
        "markdown-control": (
            lambda c: [ws(c).source_pdf, ws(c).url_vlm_markdown],
            lambda c: [ws(c).vlm_check_markdown],
        ),
        "metadata-generation": (
            lambda c: [ws(c).final_markdown, ws(c).docling_json],
            lambda c: [ws(c).final_csv, ws(c).resume_markdown, ws(c).intent_json,
                       ws(c).hyq_json, ws(c).embedding_json],
        ),
        "hyq-embedding": (
            lambda c: [ws(c).hyq_json],
            lambda c: [ws(c).hyq_dir],
        ),
    }
    return table[name]


def _converted_steps() -> dict[str, Callable[[], PipelineStep]]:
    """Étapes déjà converties en classes (lot 6) — le reste sert en ScriptStep.

    Import paresseux : les modules d'étapes peuvent tirer des dépendances
    lourdes, on ne paie que ce qu'on instancie.
    """
    from ..steps.csv_to_jsonlines import CsvToJsonlinesStep
    from ..steps.inject_image_descriptions import InjectImageDescriptionsStep
    from ..steps.load_jsonline_doctags import LoadJsonlineDoctagsStep
    from ..steps.markdown_convert import MarkdownConvertStep
    from ..steps.opencv_check import OpencvCheckStep
    from ..steps.reorder_doctags import ReorderDoctagsStep
    from ..steps.url_extraction import UrlExtractionStep

    return {
        "reorder-doctags": ReorderDoctagsStep,
        "opencv-check": OpencvCheckStep,
        "csv-to-jsonlines": CsvToJsonlinesStep,
        "load-jsonline-doctags": LoadJsonlineDoctagsStep,
        "url-extraction": UrlExtractionStep,
        "markdown-convert": MarkdownConvertStep,
        "inject-image-descriptions": InjectImageDescriptionsStep,
    }


def build_default_steps() -> list[PipelineStep]:
    """Les 13 étapes, dans l'ordre canonique de la liste ``STEPS`` legacy.

    Registre MIXTE pendant le lot 6 : une étape convertie est servie par sa
    classe, les autres restent des ``ScriptStep`` — le pipeline tourne dans
    les deux cas, c'est ce qui rend le lot interruptible.
    """
    converted = _converted_steps()
    steps: list[PipelineStep] = []
    for legacy in _LEGACY_STEPS:
        if legacy.name in converted:
            steps.append(converted[legacy.name]())
            continue
        inputs_fn, outputs_fn = _io(legacy.name)
        steps.append(
            ScriptStep(
                name=legacy.name,
                module=legacy.module,
                description=_DESCRIPTIONS[legacy.name],
                requires_vlm=legacy.name in _VLM_STEPS,
                enabled_by_default=legacy.name != "opencv-check",
                inputs_fn=inputs_fn,
                outputs_fn=outputs_fn,
            )
        )
    return steps


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
