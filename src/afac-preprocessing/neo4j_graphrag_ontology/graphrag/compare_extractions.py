"""
compare_extractions.py — Compare les 3 approches d'extraction (spaCy, VLM few-shot, VLM
structured output) sur un même document, à partir des JSON produits par
extraction_spacy.py / extraction_vlm_fewshot.py / extraction_vlm_structured.py.

Aucune des 3 méthodes ne partage plus un vocabulaire de labels fermé (cf.
extraction_schema.py — l'ontologie n'est pas encore stabilisée, les VLM choisissent leurs
propres labels et types de relation). La comparaison porte donc uniquement sur le TEXTE de
surface normalisé des entités (détectée ou non, indépendamment du label), jamais sur
l'accord de label. Les vocabulaires de labels/relations de chaque méthode sont rapportés à
part, à titre d'observation — c'est la matière première pour affiner l'ontologie plus tard.

Comparaisons produites :
  1. spaCy vs chaque approche VLM : rappel de surface (spaCy ne fait pas de relations).
  2. VLM few-shot vs VLM structured output : recouvrement des entités (texte normalisé) et
     des relations (paires source→target normalisées), + vocabulaires de labels observés.

Usage :
    uv run --active python neo4j_graphrag_ontology/graphrag/compare_extractions.py --doc-name Mineur
"""
from __future__ import annotations

import argparse

from extraction_schema import ExtractionResult, label_counts, relation_type_counts
from extraction_vlm_common import extraction_json_path
from ontology.afac_ontology import normalize_name


def load(doc_name: str, method_suffix: str) -> ExtractionResult | None:
    path = extraction_json_path(doc_name, method_suffix)
    if not path.exists():
        print(f"⚠️  Absent : {path} (lancer extraction_{method_suffix}.py --doc-name {doc_name} d'abord)")
        return None
    return ExtractionResult.model_validate_json(path.read_text(encoding="utf-8"))


def surface_recall(reference: ExtractionResult, other: ExtractionResult) -> tuple[float, list[str]]:
    """Fraction des entités de `reference` dont le texte normalisé apparaît dans `other`
    (match exact ou sous-chaîne, dans les deux sens, pour absorber les écarts de segmentation)."""
    other_texts = [e.normalized_text.lower() for e in other.entities]
    found, missing = 0, []
    for ent in reference.entities:
        needle = ent.normalized_text.lower()
        if any(needle in hay or hay in needle for hay in other_texts if hay):
            found += 1
        else:
            missing.append(ent.text)
    total = len(reference.entities) or 1
    return found / total, missing


def entity_text_set(result: ExtractionResult) -> set[str]:
    return {e.normalized_text.lower() for e in result.entities}


def relation_pair_set(result: ExtractionResult) -> set[tuple[str, str]]:
    return {(normalize_name(r.source).lower(), normalize_name(r.target).lower()) for r in result.relations}


def print_label_vocab(name: str, result: ExtractionResult) -> None:
    ent_labels = label_counts(result)
    print(f"Labels d'entités ({name}, {len(ent_labels)} distincts) : {dict(ent_labels.most_common())}")
    if result.relations:
        rel_labels = relation_type_counts(result)
        print(f"Types de relations ({name}, {len(rel_labels)} distincts) : {dict(rel_labels.most_common())}")


def compare_vlm_pair(fewshot: ExtractionResult, structured: ExtractionResult) -> None:
    print("\n--- VLM few-shot vs VLM structured output ---")
    print(f"few-shot   : {len(fewshot.entities)} entités, {len(fewshot.relations)} relations")
    print(f"structured : {len(structured.entities)} entités, {len(structured.relations)} relations")

    a, b = entity_text_set(fewshot), entity_text_set(structured)
    common, only_a, only_b = a & b, a - b, b - a
    union = a | b or {""}
    print(f"\nEntités — recouvrement (texte normalisé) : {len(common)}/{len(union)} ({len(common) / len(union):.0%})")
    if only_a:
        print(f"  Uniquement few-shot ({len(only_a)}) : {sorted(only_a)}")
    if only_b:
        print(f"  Uniquement structured ({len(only_b)}) : {sorted(only_b)}")

    ra, rb = relation_pair_set(fewshot), relation_pair_set(structured)
    r_common = ra & rb
    r_union = (ra | rb) or {("", "")}
    print(f"\nRelations — recouvrement (paires source→target) : {len(r_common)}/{len(r_union)} ({len(r_common) / len(r_union):.0%})")

    print()
    print_label_vocab("few-shot", fewshot)
    print_label_vocab("structured", structured)


def main() -> None:
    ap = argparse.ArgumentParser(description="Compare les 3 approches d'extraction sur un document.")
    ap.add_argument("--doc-name", default="Mineur")
    args = ap.parse_args()

    spacy_res = load(args.doc_name, "spacy")
    fewshot_res = load(args.doc_name, "vlm_fewshot")
    structured_res = load(args.doc_name, "vlm_structured")

    print(f"\n=== Comparaison des extractions — {args.doc_name} ===")

    for vlm_res, label in ((fewshot_res, "few-shot"), (structured_res, "structured")):
        if spacy_res and vlm_res:
            recall, missing = surface_recall(vlm_res, spacy_res)
            print(f"\n--- spaCy vs VLM {label} ---")
            print(f"Rappel de surface spaCy sur les entités VLM {label} : {recall:.0%} "
                  f"({len(vlm_res.entities) - len(missing)}/{len(vlm_res.entities)})")
            if missing:
                print(f"Non retrouvées par spaCy : {missing}")

    if fewshot_res and structured_res:
        compare_vlm_pair(fewshot_res, structured_res)


if __name__ == "__main__":
    main()