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
    with pytest.raises(StepFailed, match="Inconsistency"):
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


def test_docling_extract_reuses_one_converter_across_documents(monkeypatch) -> None:
    """Le converter est construit au 1er document puis réutilisé.

    Docling garde les poids dans converter.initialized_pipelines, un cache
    d'INSTANCE : en reconstruire un par document forçait 136 rechargements GPU
    et 272 allers-retours HuggingFace sur un batch complet. build_converter est
    remplacée par un fake — docling n'est jamais importé ici.
    """
    from afac_preprocessing.steps import docling_extract

    calls: list[tuple] = []

    def fake_build_converter(**kwargs):
        calls.append(tuple(sorted(kwargs.items(), key=lambda kv: kv[0])))
        return object()  # sentinelle : seule son identité nous intéresse

    monkeypatch.setattr(docling_extract, "build_converter", fake_build_converter)
    step = docling_extract.DoclingExtractStep()

    first = step._get_converter(extract_images=False)
    second = step._get_converter(extract_images=False)

    assert len(calls) == 1, "le converter doit être construit une seule fois"
    assert first is second


def test_docling_extract_rebuilds_converter_when_options_change(monkeypatch) -> None:
    """Une option qui change invalide le cache (sinon on servirait un converter
    aux mauvais réglages) : --no-ocr mute step.ocr, et extract_images vient du
    contexte, donc peut différer d'un document à l'autre."""
    from afac_preprocessing.steps import docling_extract

    calls: list[dict] = []

    def fake_build_converter(**kwargs):
        calls.append(kwargs)
        return object()

    monkeypatch.setattr(docling_extract, "build_converter", fake_build_converter)
    step = docling_extract.DoclingExtractStep()

    step._get_converter(extract_images=False)
    step.ocr = False  # ce que fait la CLI pour --no-ocr
    step._get_converter(extract_images=False)
    step._get_converter(extract_images=True)  # vient de settings, pas de l'instance

    assert len(calls) == 3
    assert [c["ocr"] for c in calls] == [True, False, False]
    assert [c["extract_images"] for c in calls] == [False, False, True]


def test_registry_wave_c_serves_classes() -> None:
    # Depuis la fin de la vague D, toutes les étapes sont des classes de steps/.
    for step in Pipeline.default().steps:
        assert type(step).__module__.startswith("afac_preprocessing.steps."), step.name


# --- restore_dropped_image_markers : régression "CI - Tableau des dispenses" ---
# (marqueur [[[IMAGE_DESC:N]]] absent de _vlm_check.md alors que présent dans
# _url_vlm.md, cf. restore_dropped_links pour le même phénomène sur les liens)


def test_restore_dropped_image_markers_standalone_paragraph() -> None:
    from afac_preprocessing.steps.markdown_control import restore_dropped_image_markers

    original = (
        "Tableau récapitulatif des bonnes pratiques.\n\n"
        "[[[IMAGE_DESC:2]]]\n\n"
        "31.12.1996"
    )
    corrected = (
        "Tableau récapitulatif des bonnes pratiques.\n\n"
        "<!-- image -->\n\n"
        "31.12.1996"
    )

    result = restore_dropped_image_markers(original, corrected)

    assert "[[[IMAGE_DESC:2]]]" in result
    # réinséré comme paragraphe séparé, pas collé à la phrase précédente
    assert "pratiques.\n\n[[[IMAGE_DESC:2]]]" in result


def test_restore_dropped_image_markers_inline_in_list_item() -> None:
    from afac_preprocessing.steps.markdown_control import restore_dropped_image_markers

    original = "- Contexte de l'image [[[IMAGE_DESC:3]]]\n"
    corrected = "- Contexte de l'image\n"

    result = restore_dropped_image_markers(original, corrected)

    assert result == "- Contexte de l'image[[[IMAGE_DESC:3]]]\n"


def test_restore_dropped_image_markers_already_present_is_noop() -> None:
    from afac_preprocessing.steps.markdown_control import restore_dropped_image_markers

    original = "para\n\n[[[IMAGE_DESC:1]]]\n\nsuite"
    corrected = "para\n\n[[[IMAGE_DESC:1]]]\n\nsuite"

    assert restore_dropped_image_markers(original, corrected) == corrected


def test_restore_dropped_image_markers_no_anchor_appends_at_end() -> None:
    from afac_preprocessing.steps.markdown_control import restore_dropped_image_markers

    original = "Texte totalement réécrit\n\n[[[IMAGE_DESC:5]]]\n"
    corrected = "Une page méconnaissable après correction VLM.\n"

    result = restore_dropped_image_markers(original, corrected)

    assert result.rstrip("\n").endswith("[[[IMAGE_DESC:5]]]")


def test_restore_dropped_image_markers_no_marker_is_noop() -> None:
    from afac_preprocessing.steps.markdown_control import restore_dropped_image_markers

    original = "Aucune image sur cette page."
    corrected = "Aucune image sur cette page, corrigée."

    assert restore_dropped_image_markers(original, corrected) == corrected


# --- restore_original_json_tables : régression "Analyse des inscriptions"
# (le VLM renomme les clés d'un tableau JSON injecté par load-jsonline-doctags,
# perdant le titre de colonne d'origine)


def test_restore_original_json_tables_renamed_keys() -> None:
    from afac_preprocessing.steps.markdown_control import restore_original_json_tables

    original = (
        '{"Historique des données de l\'assurance facultative": "1948- 1983", '
        '"Historique des données de l\'assurance facultative.1": "Cartes de contrôle"}\n'
        '{"Historique des données de l\'assurance facultative": "1988-2005", '
        '"Historique des données de l\'assurance facultative.1": "BUSAK"}\n'
    )
    corrected = (
        '{"Période": "1948- 1983", "Support": "Cartes de contrôle"}\n'
        '{"Période": "1988-2005", "Support": "BUSAK"}\n'
    )

    result = restore_original_json_tables(original, corrected)

    assert '"Historique des données de l\'assurance facultative": "1948- 1983"' in result
    assert '"Historique des données de l\'assurance facultative.1": "Cartes de contrôle"' in result
    assert "Période" not in result
    assert "Support" not in result


def test_restore_original_json_tables_tolerates_dash_normalization() -> None:
    # Régression réelle "Analyse des inscriptions" : le VLM a renommé les
    # clés ET remplacé le tiret par un tiret cadratin ("1983 - 1987" ->
    # "1983 – 1987") sur la même ligne. Les deux doivent être réparés.
    from afac_preprocessing.steps.markdown_control import restore_original_json_tables

    original = '{"Historique": "1983 - 1987", "Historique.1": "Microfilm"}\n'
    corrected = '{"Période": "1983 – 1987", "Support": "Microfilm"}\n'

    result = restore_original_json_tables(original, corrected)

    assert result == original


def test_restore_original_json_tables_leaves_unmatched_row_alone() -> None:
    # Une vraie différence de contenu (pas seulement typographique) : la
    # ligne ne peut pas être relocalisée en toute sécurité et reste telle
    # quelle plutôt que d'écraser à tort une correction OCR légitime.
    from afac_preprocessing.steps.markdown_control import restore_original_json_tables

    original = '{"Historique": "1983 - 1987", "Historique.1": "Microfilm"}\n'
    corrected = '{"Période": "1983 - 1987", "Support": "Microfiche"}\n'

    result = restore_original_json_tables(original, corrected)

    assert result == corrected


def test_restore_original_json_tables_already_correct_is_noop() -> None:
    from afac_preprocessing.steps.markdown_control import restore_original_json_tables

    row = '{"Historique": "1948- 1983", "Historique.1": "Cartes de contrôle"}\n'

    assert restore_original_json_tables(row, row) == row


def test_restore_original_json_tables_no_json_is_noop() -> None:
    from afac_preprocessing.steps.markdown_control import restore_original_json_tables

    original = "Aucun tableau JSON sur cette page."
    corrected = "Aucun tableau JSON sur cette page, corrigée."

    assert restore_original_json_tables(original, corrected) == corrected
