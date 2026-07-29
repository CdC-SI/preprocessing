"""Tests de contrat de la vague D (lot 6) : image-description (threads→async,
piège P5), metadata-generation + collaborateurs, hyq-embedding.

Tout passe par les fakes async — zéro réseau (contrainte C1 : pas de cache,
l'hermétisme vient des doubles).
"""

import asyncio
import csv
import json
from pathlib import Path

import pytest

from afac_preprocessing import Pipeline, PipelineContext, Settings
from afac_preprocessing.clients.fake import FakeEmbeddingClient, FakeVlmClient
from afac_preprocessing.exceptions import StepInputMissing
from afac_preprocessing.steps.hyq_embedding import HyqEmbeddingStep
from afac_preprocessing.steps.image_description import ImageDescriptionStep
from afac_preprocessing.steps.metadata_generation import MetadataGenerationStep


class _FakeBundleClients:
    """Substitut minimal du ClientBundle : même interface, clients factices."""

    def __init__(self, vlm: FakeVlmClient, emb: FakeEmbeddingClient) -> None:
        self._vlm = vlm
        self._emb = emb
        self._loop = asyncio.new_event_loop()

    def vlm(self) -> FakeVlmClient:
        return self._vlm

    def embeddings(self) -> FakeEmbeddingClient:
        return self._emb

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


DOCTAGS_2_PICS = (
    "<doctag>\n"
    "<text><loc_10><loc_10><loc_200><loc_20>Avant les images</text>\n"
    "<picture><loc_10><loc_30><loc_100><loc_60></picture>\n"
    "<picture><loc_10><loc_70><loc_100><loc_100></picture>\n"
    "<text><loc_10><loc_110><loc_200><loc_120>Après les images</text>\n"
    "<page_footer><loc_10><loc_480><loc_200><loc_490>1</page_footer>\n"
    "</doctag>\n"
)


@pytest.fixture
def ctx(tmp_path: Path):
    settings = Settings(
        vlm_url="http://vlm.local/v1",  # type: ignore[arg-type]
        vlm_model_name="qwen-vl",
        embedding_url="http://embed.local/v1",  # type: ignore[arg-type]
        embedding_model_name="bge-m3",
        project_root=tmp_path,
        data_root=tmp_path / "data",
    )
    vlm = FakeVlmClient(
        vision_response="Description de l'image.",
        structured_factory={
            "resume": "Un résumé.",
            "intent": ["comprendre", "agir"],
            "hyq": ["Question 1 ?", "Question 2 ?"],
        },
    )
    emb = FakeEmbeddingClient(embedding=[0.5, -0.25, 1.0])
    context = PipelineContext.for_pdf(
        tmp_path / "data" / "input_files" / "afac" / "Adhésion" / "Doc.pdf",
        settings,
        clients=_FakeBundleClients(vlm, emb),  # type: ignore[arg-type]
    )
    context.workspace.root.mkdir(parents=True)
    yield context, vlm, emb
    context.clients.close()


# --- image-description (P5 : ordre = ordre des images, pas des réponses) ---


def test_image_description_writes_ordered_descriptions(ctx) -> None:
    context, vlm, _ = ctx
    ws = context.workspace
    _pdf_with_pages(ws.source_pdf, 1)
    ws.reordered_with_tables_doctags.write_text(DOCTAGS_2_PICS, encoding="utf-8")

    # Réponses différentes par appel pour tracer l'ordre : le fake répond
    # dans l'ordre de soumission, l'étape doit écrire dans l'ordre des images.
    responses = iter(["Première image.", "Deuxième image."])

    async def _vision(prompt, image_b64, *, max_tokens=8192, temperature=0.0):
        vlm.calls.append(("vision_completion", (prompt, image_b64)))
        return next(responses)

    vlm.vision_completion = _vision  # type: ignore[method-assign]

    result = ImageDescriptionStep().run(context)
    assert result.ok

    md = ws.image_descriptions.read_text(encoding="utf-8")
    assert md.index("Image 1/2") < md.index("Image 2/2")
    assert md.index("Première image.") < md.index("Deuxième image.")

    out = ws.reordered_with_tables_pictures_doctags.read_text(encoding="utf-8")
    assert "[[[IMAGE_DESC:1]]]" in out and "[[[IMAGE_DESC:2]]]" in out
    assert "<picture>" not in out
    # les crops fitz ont été exportés dans used_images/
    assert any(ws.used_images_dir.glob("*.png"))


def test_image_description_no_pictures_is_passthrough(ctx) -> None:
    context, vlm, _ = ctx
    ws = context.workspace
    _pdf_with_pages(ws.source_pdf, 1)
    ws.reordered_with_tables_doctags.write_text(
        "<doctag>\n<text><loc_1><loc_2><loc_3><loc_4>Sans image</text>\n</doctag>\n",
        encoding="utf-8",
    )
    result = ImageDescriptionStep().run(context)
    assert result.ok
    assert ws.reordered_with_tables_pictures_doctags.exists()
    assert not ws.image_descriptions.exists()  # pas de fichier descriptions
    assert vlm.calls == []  # aucun appel VLM


def test_image_description_disabled_removes_tags(ctx, tmp_path: Path) -> None:
    context, vlm, _ = ctx
    context.settings.enable_image_description = False
    ws = context.workspace
    _pdf_with_pages(ws.source_pdf, 1)
    ws.reordered_with_tables_doctags.write_text(DOCTAGS_2_PICS, encoding="utf-8")
    result = ImageDescriptionStep().run(context)
    assert result.ok
    out = ws.reordered_with_tables_pictures_doctags.read_text(encoding="utf-8")
    assert "<picture>" not in out
    assert not ws.image_descriptions.exists()
    assert vlm.calls == []


def test_image_description_missing_doctags(ctx) -> None:
    context, _, _ = ctx
    with pytest.raises(StepInputMissing):
        ImageDescriptionStep().run(context)


# --- metadata-generation ---


def _prepare_metadata_inputs(context: PipelineContext) -> None:
    ws = context.workspace
    _pdf_with_pages(ws.source_pdf, 2)
    ws.final_markdown.write_text("# Doc final\n\nContenu.\n", encoding="utf-8")
    ws.docling_json.write_text(
        json.dumps({
            "origin": {"mimetype": "application/pdf"},
            "pages": {"1": {}, "2": {}},
            "tables": [],
        }),
        encoding="utf-8",
    )
    ws.hyperlinks_jsonl.write_text(
        '{"page_number": 1, "text": "site", "hyperlink": "https://exemple.ch"}\n',
        encoding="utf-8",
    )


def test_metadata_generation_writes_csv_and_enrichment(ctx) -> None:
    context, vlm, emb = ctx
    ws = context.workspace
    _prepare_metadata_inputs(context)

    result = MetadataGenerationStep().run(context)
    assert result.ok

    # Fichiers d'enrichissement
    assert ws.resume_markdown.read_text(encoding="utf-8") == "Un résumé."
    assert json.loads(ws.intent_json.read_text()) == ["comprendre", "agir"]
    assert json.loads(ws.hyq_json.read_text()) == ["Question 1 ?", "Question 2 ?"]
    assert json.loads(ws.embedding_json.read_text()) == [0.5, -0.25, 1.0]

    # CSV final : 1 en-tête + 1 ligne, colonnes contractuelles
    with open(ws.final_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert rows[0] == ["CONTENT", "METADATA", "EMBEDDING"]
    assert len(rows) == 2
    metadata = json.loads(rows[1][1])
    assert metadata["title"] == "Doc.pdf"
    assert metadata["doctype"] == "pdf"
    assert metadata["source"] == "afac"
    assert metadata["parent_label"] == ["Adhésion"]
    assert metadata["page_count"] == 2
    assert metadata["page_num"] == "1,2"
    assert metadata["resume"] == "Un résumé."
    assert metadata["intent"] == "comprendre, agir"
    assert metadata["outgoing_links"][0]["url"] == "https://exemple.ch"
    assert rows[1][2] == "0.5, -0.25, 1.0"
    # 4 appels structurés (1 resume + 3 intents + 1 hyq = 5) — vérifie le compte
    structured = [c for c in vlm.calls if c[0] == "text_completion_structured"]
    assert len(structured) == 5
    assert emb.calls == ["# Doc final\n\nContenu.\n"]


def test_metadata_generation_rerun_replaces_row(ctx) -> None:
    context, _, _ = ctx
    ws = context.workspace
    _prepare_metadata_inputs(context)
    step = MetadataGenerationStep()
    step.run(context)
    step.run(context)  # idempotent : la ligne du même titre est remplacée
    with open(ws.final_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert len(rows) == 2


def test_metadata_generation_missing_markdown(ctx) -> None:
    context, _, _ = ctx
    with pytest.raises(StepInputMissing):
        MetadataGenerationStep().run(context)


# --- hyq-embedding ---


def test_hyq_embedding_writes_one_csv_per_question(ctx) -> None:
    context, _, emb = ctx
    ws = context.workspace
    ws.metadata_dir.mkdir(parents=True)
    ws.hyq_json.write_text(
        json.dumps(["Question A ?", "Question B ?", "Question C ?"]), encoding="utf-8"
    )
    result = HyqEmbeddingStep().run(context)
    assert result.ok
    files = sorted(ws.hyq_dir.glob("question_*.csv"))
    assert [f.name for f in files] == ["question_1.csv", "question_2.csv", "question_3.csv"]
    with open(files[0], newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert rows[0] == ["CONTENT", "METADATA", "EMBEDDING"]
    assert rows[1][0] == "Question A ?"
    assert json.loads(rows[1][1]) == {"title": "Doc.pdf"}
    assert emb.calls == ["Question A ?", "Question B ?", "Question C ?"]


def test_hyq_embedding_removes_stale_question_files(ctx) -> None:
    context, _, _ = ctx
    ws = context.workspace
    ws.hyq_dir.mkdir(parents=True)
    (ws.hyq_dir / "question_9.csv").write_text("obsolète", encoding="utf-8")
    ws.hyq_json.write_text(json.dumps(["Seule question ?"]), encoding="utf-8")
    HyqEmbeddingStep().run(context)
    assert not (ws.hyq_dir / "question_9.csv").exists()
    assert (ws.hyq_dir / "question_1.csv").exists()


def test_hyq_embedding_missing_hyq_json(ctx) -> None:
    context, _, _ = ctx
    with pytest.raises(StepInputMissing):
        HyqEmbeddingStep().run(context)


# --- fin du lot 6 : plus aucun ScriptStep au registre ---


def test_no_legacy_adapter_left_in_registry() -> None:
    assert all(
        type(s).__module__.startswith("afac_preprocessing.steps.")
        for s in Pipeline.default().steps
    )


def test_six_vlm_steps_have_execute_async() -> None:
    vlm_steps = [s for s in Pipeline.default().steps if s.requires_vlm]
    assert len(vlm_steps) == 5  # image-desc, url-tuning, md-control, metadata, hyq
    assert all(hasattr(s, "_execute_async") for s in vlm_steps)
