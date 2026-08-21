"""Tests de contrat des étapes de la vague B (lot 6).

Contrat commun : sorties déclarées créées et non vides ; entrée manquante ⇒
StepInputMissing (pas SystemExit) ; comportements limites historiques
conservés (passthrough de load-jsonline sans tables/, copie d'inject sans
descriptions, etc.).
"""

from pathlib import Path

import pytest

from afac_preprocessing import Pipeline, PipelineContext, Settings
from afac_preprocessing.exceptions import StepInputMissing
from afac_preprocessing.steps.inject_image_descriptions import InjectImageDescriptionsStep
from afac_preprocessing.steps.load_jsonline_doctags import LoadJsonlineDoctagsStep
from afac_preprocessing.steps.markdown_convert import MarkdownConvertStep
from afac_preprocessing.steps.reorder_doctags import (
    ReorderDoctagsStep,
    parse_blocks,
    split_pages,
)
from afac_preprocessing.steps.url_extraction import UrlExtractionStep

DOCTAGS = (
    "<doctag>\n"
    "<text><loc_10><loc_100><loc_200><loc_120>Deuxième bloc</text>\n"
    "<text><loc_10><loc_20><loc_200><loc_40>Premier bloc</text>\n"
    "<page_footer><loc_10><loc_480><loc_200><loc_490>1</page_footer>\n"
    "</doctag>\n"
)


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
    context.workspace.root.mkdir(parents=True)
    yield context
    context.clients.close()


# --- reorder-doctags ---


def test_reorder_sorts_blocks_by_y0(ctx: PipelineContext) -> None:
    ctx.workspace.doctags.write_text(DOCTAGS, encoding="utf-8")
    result = ReorderDoctagsStep().run(ctx)
    assert result.ok
    out = ctx.workspace.reordered_doctags.read_text(encoding="utf-8")
    assert out.index("Premier bloc") < out.index("Deuxième bloc")


def test_reorder_missing_input(ctx: PipelineContext) -> None:
    with pytest.raises(StepInputMissing, match="doctags"):
        ReorderDoctagsStep().run(ctx)


# --- split_pages : arbitrage par le nombre réel de pages ---

# Forme observée sur "TN - Allègement dès 01.2025" : la page 1 n'a PAS de
# <page_footer>, les pages 2 et 3 en ont un. Le découpage par footer fusionne
# donc la page 1 dans la page 2 → 2 pages au lieu de 3.
MIXED_FOOTERS = (
    "<text><loc_10><loc_20><loc_200><loc_40>Page un</text>\n"
    "<page_break>\n"
    "<text><loc_10><loc_20><loc_200><loc_40>Page deux</text>\n"
    "<page_footer><loc_10><loc_480><loc_200><loc_490>2</page_footer>\n"
    "<page_break>\n"
    "<text><loc_10><loc_20><loc_200><loc_40>Page trois</text>\n"
    "<page_footer><loc_10><loc_480><loc_200><loc_490>3</page_footer>\n"
)


def test_split_pages_merges_page_without_footer_when_unarbitrated() -> None:
    """Comportement historique conservé quand le nombre de pages est inconnu."""
    assert len(split_pages(parse_blocks(MIXED_FOOTERS))) == 2


def test_split_pages_recovers_the_lost_page_with_expected_count() -> None:
    pages = split_pages(parse_blocks(MIXED_FOOTERS), 3)
    assert len(pages) == 3
    assert "Page un" in pages[0][0].raw


def test_split_pages_leaves_a_correct_split_untouched() -> None:
    """L'arbitrage ne doit rien changer quand le découpage par footer est déjà
    bon — sinon la correction toucherait des documents qui fonctionnent."""
    blocks = parse_blocks(DOCTAGS.replace("<doctag>\n", "").replace("</doctag>\n", ""))
    assert split_pages(blocks, 1) == split_pages(blocks)


def test_split_pages_keeps_primary_split_when_neither_strategy_matches() -> None:
    """Aucune stratégie ne donne 9 pages : on garde le découpage principal et on
    laisse markdown-control jouer son rôle de filet."""
    assert len(split_pages(parse_blocks(MIXED_FOOTERS), 9)) == 2


# --- url-extraction ---


def test_url_extraction_missing_pdf(ctx: PipelineContext) -> None:
    with pytest.raises(StepInputMissing, match="Doc.pdf"):
        UrlExtractionStep().run(ctx)


def test_url_extraction_pdf_without_links(ctx: PipelineContext) -> None:
    import fitz

    ctx.workspace.source_pdf.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    doc.new_page()
    doc.save(str(ctx.workspace.source_pdf))
    result = UrlExtractionStep().run(ctx)
    assert result.ok
    assert ctx.workspace.hyperlinks_jsonl.exists()
    assert ctx.workspace.hyperlinks_jsonl.read_text() == ""  # 0 lien = fichier vide


# --- markdown-convert ---


def test_markdown_convert_produces_url_vlm_md(ctx: PipelineContext) -> None:
    ctx.workspace.url_vlm_doctags.write_text(DOCTAGS, encoding="utf-8")
    result = MarkdownConvertStep().run(ctx)
    assert result.ok
    md = ctx.workspace.url_vlm_markdown.read_text(encoding="utf-8")
    assert "Premier bloc" in md and "Deuxième bloc" in md


def test_markdown_convert_missing_input(ctx: PipelineContext) -> None:
    with pytest.raises(StepInputMissing, match="url_vlm"):
        MarkdownConvertStep().run(ctx)


# --- load-jsonline-doctags ---


def test_load_jsonline_replaces_otsl(ctx: PipelineContext) -> None:
    ctx.workspace.reordered_doctags.write_text(
        "<doctag>\n<otsl><loc_65><loc_118><loc_435><loc_155>x</otsl>\n</doctag>\n",
        encoding="utf-8",
    )
    ctx.workspace.tables_dir.mkdir()
    (ctx.workspace.tables_dir / "Doc-table-01_page1_x65_y118_x435_y155.jsonl").write_text(
        '{"Pays": "Suisse"}\n', encoding="utf-8"
    )
    result = LoadJsonlineDoctagsStep().run(ctx)
    assert result.ok
    out = ctx.workspace.reordered_with_tables_doctags.read_text(encoding="utf-8")
    assert "<otsl>" not in out
    assert '{"Pays": "Suisse"}' in out


def test_load_jsonline_without_tables_dir_is_passthrough(ctx: PipelineContext) -> None:
    # Comportement historique : tables/ absent ⇒ copie sans modification, succès.
    ctx.workspace.reordered_doctags.write_text(DOCTAGS, encoding="utf-8")
    result = LoadJsonlineDoctagsStep().run(ctx)
    assert result.ok
    assert ctx.workspace.reordered_with_tables_doctags.read_text(encoding="utf-8") == DOCTAGS


def test_load_jsonline_missing_doctags(ctx: PipelineContext) -> None:
    with pytest.raises(StepInputMissing, match="reordered"):
        LoadJsonlineDoctagsStep().run(ctx)


# --- inject-image-descriptions ---


def test_inject_replaces_markers(ctx: PipelineContext) -> None:
    ctx.workspace.vlm_check_markdown.write_text(
        "# Titre\n\n[[[IMAGE_DESC:1]]]\n", encoding="utf-8"
    )
    ctx.workspace.image_descriptions.write_text(
        "## OK - Image 1/1 (page 1)\n\nUne description d'image.\n", encoding="utf-8"
    )
    result = InjectImageDescriptionsStep().run(ctx)
    assert result.ok
    final = ctx.workspace.final_markdown.read_text(encoding="utf-8")
    assert "[[[IMAGE_DESC:1]]]" not in final
    assert "Une description d'image." in final


def test_inject_without_descriptions_copies_as_is(ctx: PipelineContext) -> None:
    # Cas nominal : pas d'images / description désactivée ⇒ copie à l'identique.
    ctx.workspace.vlm_check_markdown.write_text("# Titre sans images\n", encoding="utf-8")
    result = InjectImageDescriptionsStep().run(ctx)
    assert result.ok
    assert ctx.workspace.final_markdown.read_text(encoding="utf-8") == "# Titre sans images\n"


def test_inject_missing_markdown(ctx: PipelineContext) -> None:
    with pytest.raises(StepInputMissing, match="vlm_check"):
        InjectImageDescriptionsStep().run(ctx)


# --- registre : les 7 converties sont servies par leurs classes ---


def test_registry_wave_b_serves_classes() -> None:
    # Toutes les étapes viennent du package steps/ (plus d'adaptateur legacy).
    for step in Pipeline.default().steps:
        assert type(step).__module__.startswith("afac_preprocessing.steps."), step.name
