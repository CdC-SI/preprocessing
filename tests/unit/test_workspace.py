"""Tests du DocumentWorkspace — chaque propriété est contractuelle (§ 2.2).

Les noms attendus viennent du relevé sur la sortie réelle du pipeline
(``find data/output_files_preprocessing/Mineur``), pas du code.
"""

from pathlib import Path

import pytest

from afac_preprocessing.settings import Settings
from afac_preprocessing.workspace import DocumentWorkspace

ROOT = Path("/out/Mineur")


@pytest.fixture
def ws() -> DocumentWorkspace:
    return DocumentWorkspace(
        doc_name="Mineur",
        source_pdf=Path("/in/afac/Adhésion/Mineur.pdf"),
        root=ROOT,
        relative_dir=Path("afac/Adhésion"),
    )


@pytest.mark.parametrize(
    ("prop", "expected"),
    [
        ("doctags", ROOT / "Mineur.doctags"),
        ("reordered_doctags", ROOT / "Mineur_reordered.doctags"),
        ("reordered_with_tables_doctags", ROOT / "Mineur_reordered_with_tables.doctags"),
        (
            "reordered_with_tables_pictures_doctags",
            ROOT / "Mineur_reordered_with_tables_pictures.doctags",
        ),
        ("url_vlm_doctags", ROOT / "Mineur_url_vlm.doctags"),
        ("markdown", ROOT / "Mineur.md"),
        ("url_vlm_markdown", ROOT / "Mineur_url_vlm.md"),
        ("vlm_check_markdown", ROOT / "Mineur_vlm_check.md"),
        ("image_descriptions", ROOT / "Mineur_image_descriptions.md"),
        ("final_markdown", ROOT / "Mineur_final.md"),
        ("final_embed_markdown", ROOT / "Mineur_final_embed.md"),
        ("docling_json", ROOT / "Mineur.json"),
        ("text_dump", ROOT / "Mineur.txt"),
        ("hyperlinks_jsonl", ROOT / "hyperlinks_data_Mineur.jsonl"),
        ("used_images_dir", ROOT / "used_images"),
        ("tables_dir", ROOT / "tables"),
        ("metadata_dir", ROOT / "metadata"),
        ("final_csv", ROOT / "metadata" / "Mineur_final.csv"),
        ("resume_markdown", ROOT / "metadata" / "resume.md"),
        ("intent_json", ROOT / "metadata" / "intent.json"),
        ("hyq_json", ROOT / "metadata" / "hyq.json"),
        ("embedding_json", ROOT / "metadata" / "embedding.json"),
        ("hyq_dir", ROOT / "metadata" / "hyq_Mineur"),
    ],
)
def test_property_follows_disk_convention(
    ws: DocumentWorkspace, prop: str, expected: Path
) -> None:
    assert getattr(ws, prop) == expected


def test_doc_name_with_accents_spaces_and_typographic_apostrophe() -> None:
    # Premier cas de test imposé par le plan : le corpus réel contient ce nom.
    name = "Cas de sortie - Prolongation d’adhésion"
    ws = DocumentWorkspace(
        doc_name=name, source_pdf=Path(f"/in/{name}.pdf"), root=Path(f"/out/{name}")
    )
    assert ws.doctags.name == f"{name}.doctags"
    assert ws.hyperlinks_jsonl.name == f"hyperlinks_data_{name}.jsonl"
    assert ws.final_csv == Path(f"/out/{name}") / "metadata" / f"{name}_final.csv"
    assert ws.hyq_dir.name == f"hyq_{name}"


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        vlm_url="http://vlm.local/v1/chat/completions",  # type: ignore[arg-type]
        project_root=tmp_path,
        data_root=tmp_path / "data",
    )


def test_for_document_computes_relative_dir_and_flat_root(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    pdf = tmp_path / "data" / "input_files" / "afac" / "Adhésion" / "Mineur.pdf"
    ws = DocumentWorkspace.for_document(pdf, settings)
    assert ws.doc_name == "Mineur"
    assert ws.relative_dir == Path("afac/Adhésion")
    # Lot 2 : root reste PLAT — relative_dir n'est consommé qu'au lot F1.
    assert ws.root == tmp_path / "data" / "output_files_preprocessing" / "Mineur"


def test_for_document_outside_input_files_gets_dot_relative_dir(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    ws = DocumentWorkspace.for_document(Path("/elsewhere/Doc.pdf"), settings)
    assert ws.relative_dir == Path(".")
    assert ws.root == tmp_path / "data" / "output_files_preprocessing" / "Doc"


def test_for_document_strips_doc_name(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    ws = DocumentWorkspace.for_document(Path("/elsewhere/ Doc .pdf"), settings)
    assert ws.doc_name == "Doc"


def test_ensure_dirs_creates_standard_tree(tmp_path: Path) -> None:
    ws = DocumentWorkspace(
        doc_name="Doc", source_pdf=Path("/in/Doc.pdf"), root=tmp_path / "out" / "Doc"
    )
    ws.ensure_dirs()
    assert ws.root.is_dir()
    assert ws.used_images_dir.is_dir()
    assert ws.tables_dir.is_dir()
    assert ws.metadata_dir.is_dir()
    ws.ensure_dirs()  # idempotent


def test_workspace_is_frozen(ws: DocumentWorkspace) -> None:
    with pytest.raises(AttributeError):
        ws.doc_name = "autre"  # type: ignore[misc]
