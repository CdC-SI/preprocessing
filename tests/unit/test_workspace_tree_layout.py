"""Tests du lot F1 — la sortie reproduit l'arborescence de l'entrée.

Le test qui compte : deux PDF homonymes rangés dans deux dossiers différents
produisent deux `root` distincts. C'est le bug d'écrasement silencieux du
§ 4bis.1, qui devient ici un test de régression permanent.
"""

from pathlib import Path

import pytest

from afac_preprocessing import PipelineContext, Settings
from afac_preprocessing.workspace import DocumentWorkspace


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        vlm_url="http://vlm.local/v1",  # type: ignore[arg-type]
        project_root=tmp_path,
        data_root=tmp_path / "data",
    )


def _ws(settings: Settings, rel: str) -> DocumentWorkspace:
    return DocumentWorkspace.for_document(settings.input_files_root / rel, settings)


# --- le cœur du lot : root suit relative_dir ---


def test_root_mirrors_input_tree(settings: Settings) -> None:
    ws = _ws(settings, "afac/Adhésion/adhésion traitement.pdf")
    assert ws.relative_dir == Path("afac/Adhésion")
    assert ws.root == (
        settings.output_files_root / "afac" / "Adhésion" / "adhésion traitement"
    )


def test_document_at_input_root_stays_flat(settings: Settings) -> None:
    ws = _ws(settings, "MonDoc.pdf")
    assert ws.relative_dir == Path()
    assert ws.root == settings.output_files_root / "MonDoc"


def test_document_outside_input_files_stays_flat(settings: Settings) -> None:
    ws = DocumentWorkspace.for_document(Path("/ailleurs/Externe.pdf"), settings)
    assert ws.relative_dir == Path()
    assert ws.root == settings.output_files_root / "Externe"


def test_deep_tree_is_preserved(settings: Settings) -> None:
    ws = _ws(settings, "afac/Taxation/DISPENSE/Annulation.pdf")
    assert ws.root == (
        settings.output_files_root / "afac" / "Taxation" / "DISPENSE" / "Annulation"
    )


# --- LE test : plus d'écrasement entre homonymes (§ 4bis.1) ---


def test_homonymous_documents_get_distinct_roots(settings: Settings) -> None:
    """Les 2 collisions réelles du corpus : avant F1, elles partageaient un
    dossier de sortie et la seconde écrasait la première."""
    a = _ws(settings, "afac/Adhésion/Liste pays UE-AELE.pdf")
    b = _ws(settings, "afac/Cas de sortie/Liste pays UE-AELE.pdf")
    assert a.root != b.root
    assert a.doc_name == b.doc_name == "Liste pays UE-AELE"
    # Chaque artefact contractuel est bien distinct, pas seulement root.
    assert a.doctags != b.doctags
    assert a.final_csv != b.final_csv
    assert a.hyq_dir != b.hyq_dir


def test_homonymous_documents_keep_identical_filenames(settings: Settings) -> None:
    """Seul l'emplacement change : les noms de fichiers restent les mêmes."""
    a = _ws(settings, "afac/Adhésion/Gestion des langues dans GEDO.pdf")
    b = _ws(settings, "afac/Gestion des dossiers/Gestion des langues dans GEDO.pdf")
    assert a.doctags.name == b.doctags.name
    assert a.final_csv.name == b.final_csv.name
    assert a.root.parent != b.root.parent


# --- les noms difficiles survivent à chaque niveau ---


def test_accents_spaces_and_apostrophes_at_every_level(settings: Settings) -> None:
    ws = _ws(settings, "afac/Cas de sortie/Cas de sortie - Prolongation d’adhésion.pdf")
    assert ws.root.parts[-2:] == (
        "Cas de sortie",
        "Cas de sortie - Prolongation d’adhésion",
    )
    assert ws.final_csv.name == "Cas de sortie - Prolongation d’adhésion_final.csv"


def test_ensure_dirs_creates_the_whole_tree(settings: Settings) -> None:
    ws = _ws(settings, "afac/Adhésion/Mineur.pdf")
    ws.ensure_dirs()
    assert ws.root.is_dir()
    assert ws.metadata_dir.is_dir()
    assert ws.tables_dir.is_dir()


def test_context_for_pdf_uses_the_tree_layout(settings: Settings) -> None:
    pdf = settings.input_files_root / "afac" / "Adhésion" / "Mineur.pdf"
    ctx = PipelineContext.for_pdf(pdf, settings)
    assert ctx.workspace.root == (
        settings.output_files_root / "afac" / "Adhésion" / "Mineur"
    )
    ctx.clients.close()
