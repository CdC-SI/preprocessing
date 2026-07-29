"""Tests du lot F3 — `title` sans extension, `doctype` = extension seule.

Le champ garde son nom (`doctype`) ; seule sa source change : l'extension du
chemin au lieu du mimetype Docling (décision n°9).
"""

import json
from pathlib import Path

import pytest

from afac_preprocessing.steps.metadata_generation import build_metadata

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
    out = tmp_path / "out"
    if doc_json is not None:
        doc_name = Path(relative_doc_path).stem
        (out / doc_name).mkdir(parents=True, exist_ok=True)
        (out / doc_name / f"{doc_name}.json").write_text(
            json.dumps(doc_json), encoding="utf-8"
        )
    out.mkdir(parents=True, exist_ok=True)
    return build_metadata(
        relative_doc_path,
        folder_source=tmp_path / "in",
        input_dir=out,
        image_dir=out,
        url_dir=out,
        markdown_dir=out,
        embedding_model_name="bge-m3",
    )


# --- title : nom complet du fichier, sans extension ---


@pytest.mark.parametrize(
    ("relative_doc_path", "expected_title"),
    [
        ("afac/Adhésion/adhésion traitement.pdf", "adhésion traitement"),
        ("afac/Adhésion/notice_v2.docx", "notice_v2"),
        # Nom contenant des points : seule la dernière extension tombe.
        ("afac/Adhésion/notice.v2.final.pdf", "notice.v2.final"),
        # Accents, espaces et apostrophe typographique préservés.
        ("afac/Cas de sortie/Cas de sortie - Prolongation d’adhésion.pdf",
         "Cas de sortie - Prolongation d’adhésion"),
        ("MonDoc.pdf", "MonDoc"),
    ],
)
def test_title_has_no_extension(
    tmp_path: Path, relative_doc_path: str, expected_title: str
) -> None:
    assert _build(tmp_path, relative_doc_path)["title"] == expected_title


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
    # Le contenu des clés non touchées par F3 reste ce qu'il était.
    assert meta["source"] == "afac"
    assert meta["parent_label"] == ["Adhésion"]
    assert meta["language"] == "fr"
    assert meta["visibility"] == "internal"
    assert meta["page_count"] == 2
    assert meta["page_num"] == "1,2"
    assert meta["embedding_model"] == "bge-m3"
    assert meta["content"] == "MonDoc_final.md"
    assert meta["chunk_count"] == 0
    assert meta["outgoing_links"] == []
    assert meta["incoming_links"] == []


def test_uuid_is_stable_across_runs(tmp_path: Path) -> None:
    """L'uuid dérive du chemin relatif, pas du titre — F3 ne le change pas."""
    a = _build(tmp_path, "afac/Adhésion/MonDoc.pdf")["uuid"]
    b = _build(tmp_path, "afac/Adhésion/MonDoc.pdf")["uuid"]
    assert a == b
