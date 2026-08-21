"""Tests de contrat de l'étape csv-to-jsonlines (vague A — le patron).

Le contrat vérifié pour chaque étape convertie (recette du lot 6) :
sorties déclarées créées et non vides ; tables/ manquant ⇒ passthrough
(un document sans tableau n'est pas une erreur) ; idempotence.
"""

import json
from pathlib import Path

import pytest

from afac_preprocessing import Pipeline, PipelineContext, Settings
from afac_preprocessing.core.step import StepStatus
from afac_preprocessing.exceptions import StepFailed
from afac_preprocessing.steps.csv_to_jsonlines import CsvToJsonlinesStep


@pytest.fixture
def ctx(tmp_path: Path) -> PipelineContext:
    settings = Settings(
        vlm_url="http://vlm.local/v1",  # type: ignore[arg-type]
        project_root=tmp_path,
        data_root=tmp_path / "data",
    )
    context = PipelineContext.for_pdf(
        tmp_path / "data" / "input_files" / "Doc.pdf", settings
    )
    yield context
    context.clients.close()


def _write_table(ctx: PipelineContext, name: str = "Doc-table-01.csv") -> Path:
    ctx.workspace.tables_dir.mkdir(parents=True)
    csv = ctx.workspace.tables_dir / name
    csv.write_text("Pays,Code\nSuisse,CH\nFrance,FR\n", encoding="utf-8")
    return csv


def test_outputs_created_and_non_empty(ctx: PipelineContext) -> None:
    _write_table(ctx)
    result = CsvToJsonlinesStep().run(ctx)
    assert result.ok
    jsonl = ctx.workspace.tables_dir / "Doc-table-01.jsonl"
    assert jsonl.exists() and jsonl.stat().st_size > 0
    rows = [json.loads(line) for line in jsonl.read_text().splitlines()]
    assert rows == [{"Pays": "Suisse", "Code": "CH"}, {"Pays": "France", "Code": "FR"}]


def test_missing_tables_dir_is_passthrough_not_an_error(
    ctx: PipelineContext,
) -> None:
    result = CsvToJsonlinesStep().run(ctx)  # tables/ n'existe pas
    assert result.ok
    assert result.status is StepStatus.PASSTHROUGH


def test_empty_tables_dir_fails_like_legacy_exit_1(ctx: PipelineContext) -> None:
    ctx.workspace.tables_dir.mkdir(parents=True)
    with pytest.raises(StepFailed, match="No CSV files"):
        CsvToJsonlinesStep().run(ctx)


def test_idempotent_rerun_produces_same_output(ctx: PipelineContext) -> None:
    _write_table(ctx)
    step = CsvToJsonlinesStep()
    step.run(ctx)
    first = (ctx.workspace.tables_dir / "Doc-table-01.jsonl").read_bytes()
    step.run(ctx)
    assert (ctx.workspace.tables_dir / "Doc-table-01.jsonl").read_bytes() == first


def test_registry_serves_the_class(tmp_path: Path) -> None:
    step = next(s for s in Pipeline.default().steps if s.name == "csv-to-jsonlines")
    assert isinstance(step, CsvToJsonlinesStep)
