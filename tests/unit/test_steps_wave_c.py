"""Tests de contrat de la vague C (lot 6) : docling-extract (déclarations),
url-tuning et markdown-control (async, via FakeVlmClient — zéro réseau).

Le contrat VLM vérifié (recette lot 6, point 9) : le fake async est bien
attendu — la coroutine passe par ctx.run_async — et jamais appelé en sync.
"""

from pathlib import Path

import pytest

from afac_preprocessing import Pipeline, PipelineContext, Settings
from afac_preprocessing.clients.fake import FakeVlmClient
from afac_preprocessing.exceptions import StepFailed, StepInputMissing
from afac_preprocessing.steps.markdown_control import MarkdownControlStep
from afac_preprocessing.steps.url_tuning import UrlTuningStep


class _FakeBundleClients:
    """Substitut minimal du ClientBundle : même interface, VLM factice."""

    def __init__(self, vlm: FakeVlmClient) -> None:
        import asyncio

        self._vlm = vlm
        self._loop = asyncio.new_event_loop()

    def vlm(self) -> FakeVlmClient:
        return self._vlm

    def embeddings(self):  # pragma: no cover - non utilisé en vague C
        raise AssertionError("embeddings() ne doit pas être appelé ici")

    def run_async(self, coro):
        return self._loop.run_until_complete(coro)

    @property
    def loop(self):
        return self._loop

    def close(self) -> None:
        self._loop.close()


def _pdf_with_pages(path: Path, n: int) -> None:
    import fitz

    doc = fitz.open()
    for _ in range(n):
        doc.new_page()
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))


@pytest.fixture
def ctx(tmp_path: Path):
    settings = Settings(
        vlm_url="http://vlm.local/v1",  # type: ignore[arg-type]
        vlm_model_name="qwen-vl",
        project_root=tmp_path,
        data_root=tmp_path / "data",
    )
    fake = FakeVlmClient(vision_response="<text><loc_1><loc_2>corrigé</text>")
    context = PipelineContext.for_pdf(
        tmp_path / "data" / "input_files" / "Doc.pdf",
        settings,
        clients=_FakeBundleClients(fake),  # type: ignore[arg-type]
    )
    context.workspace.root.mkdir(parents=True)
    yield context, fake
    context.clients.close()


# --- url-tuning ---


def test_url_tuning_calls_fake_vlm_and_writes_output(ctx) -> None:
    context, fake = ctx
    ws = context.workspace
    _pdf_with_pages(ws.source_pdf, 1)
    ws.reordered_with_tables_pictures_doctags.write_text(
        "<doctag>\n<text><loc_1><loc_2>contenu</text>\n</doctag>\n", encoding="utf-8"
    )
    ws.hyperlinks_jsonl.write_text(
        '{"page_number": 1, "text": "lien", "hyperlink": "https://x.ch"}\n', encoding="utf-8"
    )

    result = UrlTuningStep().run(context)
    assert result.ok
    out = ws.url_vlm_doctags.read_text(encoding="utf-8")
    assert "corrigé" in out  # la réponse du fake est bien intégrée
    # check_connectivity puis 1 vision_completion (1 page)
    assert [c[0] for c in fake.calls] == ["check_connectivity", "vision_completion"]
    # le prompt contient bien le lien de la page
    assert "https://x.ch" in str(fake.calls[1][1][0])


def test_url_tuning_unreachable_vlm_fails_cleanly(ctx) -> None:
    context, fake = ctx
    fake.reachable = False
    ws = context.workspace
    _pdf_with_pages(ws.source_pdf, 1)
    ws.reordered_with_tables_pictures_doctags.write_text("<doctag>x</doctag>", encoding="utf-8")
    ws.hyperlinks_jsonl.write_text("", encoding="utf-8")
    with pytest.raises(StepFailed, match="VLM unreachable"):
        UrlTuningStep().run(context)


def test_url_tuning_missing_inputs(ctx) -> None:
    context, _ = ctx
    with pytest.raises(StepInputMissing):
        UrlTuningStep().run(context)


# --- markdown-control ---


def test_markdown_control_page_count_mismatch_fails(ctx) -> None:
    context, _ = ctx
    ws = context.workspace
    _pdf_with_pages(ws.source_pdf, 2)  # 2 pages PDF
    ws.url_vlm_markdown.write_text("# Une seule page markdown\n", encoding="utf-8")
    with pytest.raises(StepFailed, match="Incohérence"):
        MarkdownControlStep().run(context)


def test_markdown_control_writes_corrected_markdown(ctx) -> None:
    context, fake = ctx
    fake.vision_response = "```markdown\n# Page corrigée\n```"
    ws = context.workspace
    _pdf_with_pages(ws.source_pdf, 2)
    ws.url_vlm_markdown.write_text(
        "# Page 1\n\n<!-- page-break -->\n\n# Page 2\n", encoding="utf-8"
    )

    result = MarkdownControlStep().run(context)
    assert result.ok
    out = ws.vlm_check_markdown.read_text(encoding="utf-8")
    # les code fences de Qwen sont bien strippées, 2 pages jointes
    assert out == "# Page corrigée\n\n# Page corrigée"
    assert [c[0] for c in fake.calls].count("vision_completion") == 2


# --- docling-extract : déclarations et registre (l'exécution réelle est
# vérifiée par le rejeu disque, pas en test unitaire — torch/docling) ---


def test_docling_extract_declared_io(ctx) -> None:
    context, _ = ctx
    from afac_preprocessing.steps.docling_extract import DoclingExtractStep

    step = DoclingExtractStep()
    ws = context.workspace
    assert step.inputs(context) == [ws.source_pdf]
    assert ws.doctags in step.outputs(context)
    assert ws.markdown in step.outputs(context)
    assert step.ocr is True and step.device == "cuda" and step.threads == 4


def test_registry_wave_c_serves_classes() -> None:
    from afac_preprocessing.core.script_step import ScriptStep

    still_scripts = {
        s.name for s in Pipeline.default().steps if isinstance(s, ScriptStep)
    }
    # Après la vague C, seules les 3 étapes de la vague D restent en ScriptStep.
    assert still_scripts == {"image-description", "metadata-generation", "hyq-embedding"}
