"""Tests de Pipeline.select() (sémantique levée de _select_steps) et du
test de câblage inputs/outputs (§ 2.2 du plan)."""

from pathlib import Path

import pytest

from afac_preprocessing import Pipeline, PipelineContext, Settings
from afac_preprocessing.exceptions import UnknownStep

ALL_NAMES = [
    "docling-extract", "reorder-doctags", "opencv-check", "csv-to-jsonlines",
    "load-jsonline-doctags", "image-description", "url-extraction", "url-tuning",
    "markdown-convert", "markdown-control", "inject-image-descriptions",
    "metadata-generation", "hyq-embedding",
]


def _names(pipeline: Pipeline) -> list[str]:
    return [step.name for step in pipeline.steps]


def test_default_has_the_13_steps_in_order() -> None:
    assert _names(Pipeline.default()) == ALL_NAMES


def test_select_excludes_opencv_check_by_default() -> None:
    selected = Pipeline.default().select()
    assert "opencv-check" not in _names(selected)
    assert len(selected.steps) == 12


def test_select_include_disabled_keeps_opencv_check() -> None:
    assert _names(Pipeline.default().select(include_disabled=True)) == ALL_NAMES


def test_select_only_single_step() -> None:
    assert _names(Pipeline.default().select(only=["markdown-control"])) == ["markdown-control"]


def test_select_only_explicit_includes_disabled_step() -> None:
    # Nommer explicitement une étape désactivée vaut opt-in (comme --only aujourd'hui).
    assert _names(Pipeline.default().select(only=["opencv-check"])) == ["opencv-check"]


def test_select_only_is_always_in_pipeline_order() -> None:
    selected = Pipeline.default().select(only=["markdown-convert", "docling-extract", "url-tuning"])
    assert _names(selected) == ["docling-extract", "url-tuning", "markdown-convert"]


def test_select_only_accepts_numbers_like_today() -> None:
    # Les numéros 1-based restent acceptés (compat avec l'orchestrateur actuel).
    assert _names(Pipeline.default().select(only=["1", "9"])) == [
        "docling-extract", "markdown-convert",
    ]


def test_select_from_to_range() -> None:
    selected = Pipeline.default().select(from_="markdown-convert", to="markdown-control")
    assert _names(selected) == ["markdown-convert", "markdown-control"]


def test_select_from_greater_than_to_is_empty() -> None:
    assert Pipeline.default().select(from_="markdown-control", to="docling-extract").steps == []


def test_select_skip_by_name() -> None:
    selected = Pipeline.default().select(skip=["image-description", "hyq-embedding"])
    names = _names(selected)
    assert "image-description" not in names
    assert "hyq-embedding" not in names
    assert len(names) == 10


def test_select_unknown_name_lists_valid_steps() -> None:
    with pytest.raises(UnknownStep) as excinfo:
        Pipeline.default().select(only=["pas-une-etape"])
    message = str(excinfo.value)
    assert "pas-une-etape" in message
    assert "1=docling-extract" in message
    assert "13=hyq-embedding" in message


def test_select_is_pure_and_chainable() -> None:
    base = Pipeline.default()
    base.select(skip=["url-tuning"])
    assert len(base.steps) == 13  # l'original n'est pas modifié
    chained = base.select(to="markdown-convert").select(skip=["reorder-doctags"])
    assert "reorder-doctags" not in _names(chained)


# --- test de câblage (§ 2.2) : le mauvais chaînage devient impossible ---


def test_wiring_every_input_is_produced_upstream(tmp_path: Path) -> None:
    """Pour chaque étape n : inputs(n) ⊆ ⋃ outputs(1..n-1) ∪ {source_pdf}."""
    settings = Settings(
        vlm_url="http://vlm.local/v1",  # type: ignore[arg-type]
        project_root=tmp_path,
        data_root=tmp_path / "data",
    )
    pdf = tmp_path / "data" / "input_files" / "afac" / "Adhésion" / "Mineur.pdf"
    ctx = PipelineContext.for_pdf(pdf, settings)

    produced: set[Path] = {ctx.workspace.source_pdf}
    for step in Pipeline.default().steps:
        missing = [p for p in step.inputs(ctx) if p not in produced]
        assert not missing, (
            f"Étape '{step.name}' : entrées non produites en amont : {missing}"
        )
        produced.update(step.outputs(ctx))
    ctx.clients.close()
