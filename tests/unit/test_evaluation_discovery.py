"""Découverte des documents par la baseline et l'évaluation (lot 9).

Ces modules supposaient ``stage5_dir / <doc>`` — le layout PLAT d'avant F1.
Depuis que la sortie reproduit l'arborescence d'entrée, cette hypothèse ne
trouvait plus rien : la baseline comme l'évaluation retournaient
silencieusement 0 document, sans erreur. Ces tests verrouillent les deux
layouts.
"""

import csv
import json
from pathlib import Path

from afac_preprocessing.pipeline_baseline.single_docling_baseline import discover_doc_names
from afac_preprocessing.retrieval_protocol_evaluation.loaders import (
    load_all_doc_embeddings,
    load_hyq_questions,
    resolve_doc_dir,
)

EMBEDDING = "0.1, 0.2, 0.3"


def _make_doc(doc_dir: Path, name: str) -> None:
    """Sortie minimale d'un document : markdown brut, CSV final, questions HyQ."""
    hyq_dir = doc_dir / "metadata" / f"hyq_{name}"
    hyq_dir.mkdir(parents=True)
    (doc_dir / f"{name}.md").write_text(f"markdown brut {name}", encoding="utf-8")

    with (doc_dir / "metadata" / f"{name}_final.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["CONTENT", "METADATA", "EMBEDDING"])
        writer.writerow(["contenu", "{}", EMBEDDING])

    (doc_dir / "metadata" / "hyq.json").write_text(
        json.dumps(["Question 1 ?", "Question 2 ?"]), encoding="utf-8"
    )
    for idx in (1, 2):
        with (hyq_dir / f"question_{idx}.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["CONTENT", "EMBEDDING"])
            writer.writerow([f"Question {idx} ?", EMBEDDING])


def _corpus(root: Path) -> None:
    _make_doc(root / "afac" / "Adhésion" / "Mineur", "Mineur")       # arborescence F1
    _make_doc(root / "afac" / "Taxation" / "Dispense", "Dispense")   # autre thème
    _make_doc(root / "AncienDoc", "AncienDoc")                       # layout plat, pré-F1


def test_resolve_doc_dir_finds_document_in_mirror_tree(tmp_path: Path) -> None:
    _corpus(tmp_path)
    assert resolve_doc_dir(tmp_path, "Mineur") == tmp_path / "afac" / "Adhésion" / "Mineur"


def test_resolve_doc_dir_still_finds_flat_layout(tmp_path: Path) -> None:
    """Les sorties produites avant F1 restent lisibles."""
    _corpus(tmp_path)
    assert resolve_doc_dir(tmp_path, "AncienDoc") == tmp_path / "AncienDoc"


def test_discover_doc_names_sees_every_theme(tmp_path: Path) -> None:
    _corpus(tmp_path)
    assert sorted(discover_doc_names(tmp_path)) == ["AncienDoc", "Dispense", "Mineur"]


def test_load_all_doc_embeddings_is_recursive(tmp_path: Path) -> None:
    _corpus(tmp_path)
    names = sorted(record.doc_name for record in load_all_doc_embeddings(tmp_path))
    assert names == ["AncienDoc", "Dispense", "Mineur"]


def test_load_hyq_questions_resolves_nested_document(tmp_path: Path) -> None:
    _corpus(tmp_path)
    questions = load_hyq_questions(tmp_path, "Dispense")
    assert [q.content for q in questions] == ["Question 1 ?", "Question 2 ?"]
    assert questions[0].source_doc_title == "Dispense.pdf"
