"""Tests du lot F3 — `title` avec extension, `doctype` = extension seule.

Le champ `doctype` garde son nom ; seule sa source change : l'extension du
chemin au lieu du mimetype Docling (décision n°9). `title` inclut désormais
l'extension (dérivée de `doctype`) pour rester cohérent avec la convention du
backend Java côté upload personnel. outgoing_links, incoming_links,
media_type, parent_label, children_label et sibling sont désormais des
chaînes JSON (et non des listes natives), pour rester compatibles avec le
typage `Map<String,String>` du backend.
"""

import json
from pathlib import Path

import pytest

from afac_preprocessing import Settings
from afac_preprocessing.steps.metadata_generation import build_metadata
from afac_preprocessing.workspace import DocumentWorkspace

# Les 22 clés produites par build_metadata (resume/intent/hyq sont ajoutées
# après coup par l'étape, pas par cette fonction).
EXPECTED_KEYS = {
    "uuid", "user_uuid", "source", "title", "doctype", "version", "visibility",
    "language", "outgoing_links", "incoming_links", "created_at", "updated_at",
    "media_type", "parent_label", "children_label", "sibling", "content",
    "page_count", "page_num", "chunk_count", "embedding_model",
}


def _build(tmp_path: Path, relative_doc_path: str, doc_json: dict | None = None) -> dict:
    """build_metadata sur une arborescence minimale ; doc_json optionnel."""
    settings = Settings(
        vlm_url="http://vlm.local/v1",  # type: ignore[arg-type]
        project_root=tmp_path,
        data_root=tmp_path / "data",
    )
    workspace = DocumentWorkspace.for_document(
        settings.input_files_root / relative_doc_path, settings
    )
    settings.output_files_root.mkdir(parents=True, exist_ok=True)
    if doc_json is not None:
        workspace.root.mkdir(parents=True, exist_ok=True)
        workspace.docling_json.write_text(json.dumps(doc_json), encoding="utf-8")
    return build_metadata(
        relative_doc_path,
        folder_source=settings.input_files_root,
        workspace=workspace,
        out_root=settings.output_files_root,
        embedding_model_name="bge-m3",
    )


# --- title : nom complet du fichier, AVEC extension ---


@pytest.mark.parametrize(
    ("relative_doc_path", "expected_title"),
    [
        ("afac/Adhésion/adhésion traitement.pdf", "adhésion traitement.pdf"),
        ("afac/Adhésion/notice_v2.docx", "notice_v2.docx"),
        # Nom contenant des points : seule la dernière extension tombe du stem,
        # puis est rajoutée telle quelle.
        ("afac/Adhésion/notice.v2.final.pdf", "notice.v2.final.pdf"),
        # Accents, espaces et apostrophe typographique préservés.
        ("afac/Cas de sortie/Cas de sortie - Prolongation d’adhésion.pdf",
         "Cas de sortie - Prolongation d’adhésion.pdf"),
        ("MonDoc.pdf", "MonDoc.pdf"),
    ],
)
def test_title_has_extension(
    tmp_path: Path, relative_doc_path: str, expected_title: str
) -> None:
    assert _build(tmp_path, relative_doc_path)["title"] == expected_title


def test_title_has_no_trailing_dot_when_extensionless(tmp_path: Path) -> None:
    """Pas d'extension -> pas de point final ajouté au title."""
    meta = _build(tmp_path, "afac/Adhésion/sans_extension")
    assert meta["title"] == "sans_extension"
    assert meta["doctype"] == ""


# --- doctype : extension seule, normalisée ---


@pytest.mark.parametrize(
    ("relative_doc_path", "expected_doctype"),
    [
        ("afac/Adhésion/MonDoc.pdf", "pdf"),
        ("afac/Adhésion/MonDoc.PDF", "pdf"),      # normalisé en minuscules
        ("afac/Adhésion/notice.docx", "docx"),
        ("afac/Adhésion/notes.txt", "txt"),
        ("afac/Adhésion/notice.v2.final.pdf", "pdf"),
        ("afac/Adhésion/sans_extension", ""),     # pas d'extension → chaîne vide
    ],
)
def test_doctype_is_the_bare_extension(
    tmp_path: Path, relative_doc_path: str, expected_doctype: str
) -> None:
    assert _build(tmp_path, relative_doc_path)["doctype"] == expected_doctype


def test_doctype_no_longer_depends_on_docling_json(tmp_path: Path) -> None:
    """Gain de robustesse du lot F3 : sans JSON Docling, l'ancien get_doctype
    renvoyait "unknown" ; l'extension donne toujours la bonne réponse."""
    assert _build(tmp_path, "afac/Adhésion/MonDoc.pdf")["doctype"] == "pdf"


def test_doctype_ignores_a_wrong_docling_mimetype(tmp_path: Path) -> None:
    meta = _build(
        tmp_path,
        "afac/Adhésion/MonDoc.pdf",
        doc_json={"origin": {"mimetype": "application/octet-stream"}, "pages": {}},
    )
    assert meta["doctype"] == "pdf"


# --- non-régression : les 20 autres clés ne bougent pas ---


def test_other_keys_are_unchanged(tmp_path: Path) -> None:
    meta = _build(
        tmp_path,
        "afac/Adhésion/MonDoc.pdf",
        doc_json={"origin": {"mimetype": "application/pdf"},
                  "pages": {"1": {}, "2": {}}, "tables": []},
    )
    assert set(meta) == EXPECTED_KEYS
    # Le contenu des clés non touchées par F3 reste ce qu'il était (hormis le
    # ré-encodage JSON des champs listes, requis par le typage backend).
    assert meta["source"] == "afac"
    assert meta["parent_label"] == json.dumps(["Adhésion"], ensure_ascii=False)
    assert meta["language"] == "fr"
    assert meta["visibility"] == "internal"
    assert meta["page_count"] == 2
    assert meta["page_num"] == "1,2"
    assert meta["embedding_model"] == "bge-m3"
    assert meta["content"] == "MonDoc_final.md"
    assert meta["chunk_count"] == 0
    assert meta["outgoing_links"] == "[]"
    assert meta["incoming_links"] == "[]"


def test_uuid_is_stable_across_runs(tmp_path: Path) -> None:
    """L'uuid dérive du chemin relatif, pas du titre — F3 ne le change pas."""
    a = _build(tmp_path, "afac/Adhésion/MonDoc.pdf")["uuid"]
    b = _build(tmp_path, "afac/Adhésion/MonDoc.pdf")["uuid"]
    assert a == b
