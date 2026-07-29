"""Tests du lot F2 — CSV global par dossier racine.

Le test le plus important : l'idempotence. La reconstruction complète (jamais
un append) garantit qu'un rerun produit un fichier identique et qu'un document
supprimé disparaît de l'agrégat.
"""

import csv
import json
import sys
from pathlib import Path

import pytest

from afac_preprocessing.aggregate import (
    CSV_HEADER,
    CSV_QUOTING,
    aggregate_all_roots,
    aggregate_root_csv,
    discover_roots,
    find_document_csvs,
)


def _write_doc_csv(out_root: Path, rel_dir: str, doc: str, embedding: str = "0.1, 0.2") -> Path:
    """Écrit un CSV par document au format du pipeline."""
    path = out_root / rel_dir / doc / "metadata" / f"{doc}_final.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, quoting=CSV_QUOTING)
        writer.writerow(CSV_HEADER)
        writer.writerow([f"# {doc}\n\nContenu.", json.dumps({"title": doc}), embedding])
    return path


def _read(path: Path) -> list[list[str]]:
    csv.field_size_limit(sys.maxsize)
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.reader(fh))


@pytest.fixture
def out_root(tmp_path: Path) -> Path:
    root = tmp_path / "output_files_preprocessing"
    root.mkdir()
    return root


def test_three_documents_give_one_header_and_three_rows(out_root: Path) -> None:
    for rel, doc in [("afac/Adhésion", "B"), ("afac/Adhésion", "A"), ("afac/Sortie", "C")]:
        _write_doc_csv(out_root, rel, doc)

    path = aggregate_root_csv(out_root, "afac")
    rows = _read(path)
    assert path == out_root / "afac" / "afac.csv"
    assert rows[0] == CSV_HEADER
    assert len(rows) == 4  # 1 en-tête + 3 lignes


def test_rows_follow_relative_path_order(out_root: Path) -> None:
    """L'ordre de traitement du batch est sorted(rglob("*.pdf")) : le tri par
    chemin relatif le reproduit — pas le tri par nom de fichier."""
    for rel, doc in [("afac/Sortie", "A"), ("afac/Adhésion", "Z")]:
        _write_doc_csv(out_root, rel, doc)

    rows = _read(aggregate_root_csv(out_root, "afac"))
    titles = [json.loads(r[1])["title"] for r in rows[1:]]
    # "Adhésion/Z…" précède "Sortie/A…" par chemin, alors que par nom de
    # fichier ce serait l'inverse.
    assert titles == ["Z", "A"]


def test_rerun_is_idempotent(out_root: Path) -> None:
    _write_doc_csv(out_root, "afac/Adhésion", "A")
    _write_doc_csv(out_root, "afac/Adhésion", "B")

    first = aggregate_root_csv(out_root, "afac").read_bytes()
    second = aggregate_root_csv(out_root, "afac").read_bytes()
    third = aggregate_root_csv(out_root, "afac").read_bytes()
    assert first == second == third


def test_deleted_document_disappears_from_aggregate(out_root: Path) -> None:
    a = _write_doc_csv(out_root, "afac/Adhésion", "A")
    _write_doc_csv(out_root, "afac/Adhésion", "B")
    aggregate_root_csv(out_root, "afac")

    a.unlink()
    rows = _read(aggregate_root_csv(out_root, "afac"))
    titles = [json.loads(r[1])["title"] for r in rows[1:]]
    assert titles == ["B"]  # reconstruction, pas append


def test_empty_root_gives_header_only_without_raising(out_root: Path) -> None:
    (out_root / "afac").mkdir()
    rows = _read(aggregate_root_csv(out_root, "afac"))
    assert rows == [CSV_HEADER]


def test_missing_root_is_created_with_header_only(out_root: Path) -> None:
    path = aggregate_root_csv(out_root, "inexistant")
    assert path.exists()
    assert _read(path) == [CSV_HEADER]


def test_long_embedding_and_quoted_metadata_survive_a_roundtrip(out_root: Path) -> None:
    """Colonne EMBEDDING de plusieurs Ko et METADATA contenant guillemets et
    retours à la ligne : le fichier doit rester relisible par csv.reader."""
    long_embedding = ", ".join(f"{i / 7:.6f}" for i in range(5000))
    tricky = json.dumps({
        "title": 'Doc "spécial"',
        "resume": "Ligne 1\nLigne 2 avec ; et , et \"guillemets\"",
    })
    path = out_root / "afac" / "Adhésion" / "Doc" / "metadata" / "Doc_final.csv"
    path.parent.mkdir(parents=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, quoting=CSV_QUOTING)
        writer.writerow(CSV_HEADER)
        writer.writerow(["Contenu\navec saut", tricky, long_embedding])

    rows = _read(aggregate_root_csv(out_root, "afac"))
    assert len(rows) == 2
    assert rows[1][2] == long_embedding
    assert json.loads(rows[1][1])["title"] == 'Doc "spécial"'
    assert "\n" in rows[1][0]


def test_two_roots_give_two_independent_csvs(out_root: Path) -> None:
    _write_doc_csv(out_root, "afac/Adhésion", "A")
    _write_doc_csv(out_root, "autre/Theme", "B")

    assert discover_roots(out_root) == ["afac", "autre"]
    written = aggregate_all_roots(out_root)
    assert [p.name for p in written] == ["afac.csv", "autre.csv"]

    afac_titles = [json.loads(r[1])["title"] for r in _read(written[0])[1:]]
    autre_titles = [json.loads(r[1])["title"] for r in _read(written[1])[1:]]
    assert afac_titles == ["A"]
    assert autre_titles == ["B"]


def test_global_csv_is_not_swallowed_by_a_rerun(out_root: Path) -> None:
    """Le CSV global vit dans le sous-arbre qu'il agrège : il ne doit jamais
    se ré-agréger lui-même."""
    _write_doc_csv(out_root, "afac/Adhésion", "A")
    aggregate_root_csv(out_root, "afac")
    rows = _read(aggregate_root_csv(out_root, "afac"))
    assert len(rows) == 2  # toujours 1 en-tête + 1 ligne


def test_find_document_csvs_ignores_other_files(out_root: Path) -> None:
    _write_doc_csv(out_root, "afac/Adhésion", "A")
    doc_dir = out_root / "afac" / "Adhésion" / "A"
    (doc_dir / "metadata" / "hyq_A").mkdir(parents=True, exist_ok=True)
    (doc_dir / "metadata" / "hyq_A" / "question_1.csv").write_text("x", encoding="utf-8")
    (doc_dir / "A.md").write_text("x", encoding="utf-8")

    found = find_document_csvs(out_root / "afac")
    assert [p.name for p in found] == ["A_final.csv"]


def test_flat_layout_document_dirs_are_not_mistaken_for_roots(out_root: Path) -> None:
    """Une sortie produite avant le lot F1 est plate : ses dossiers documents
    sont des enfants directs de la racine. Les prendre pour des corpus créerait
    un CSV « global » parasite dans chacun d'eux."""
    # Document au layout PLAT : <out>/<doc>/metadata/<doc>_final.csv
    flat = out_root / "Mineur" / "metadata" / "Mineur_final.csv"
    flat.parent.mkdir(parents=True)
    with flat.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, quoting=CSV_QUOTING)
        writer.writerow(CSV_HEADER)
        writer.writerow(["c", json.dumps({"title": "Mineur"}), "0.1"])
    # Document au layout ARBORESCENT sous un vrai corpus
    _write_doc_csv(out_root, "afac/Adhésion", "A")

    assert discover_roots(out_root) == ["afac"]
    written = aggregate_all_roots(out_root)
    assert [p.name for p in written] == ["afac.csv"]
    assert not (out_root / "Mineur" / "Mineur.csv").exists()
