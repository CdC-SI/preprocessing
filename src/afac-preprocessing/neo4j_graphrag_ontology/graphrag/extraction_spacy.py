"""
extraction_spacy.py — Approche 1/3 : extraction d'entités nommées (NER) avec spaCy.

Sert de baseline « générique » avant de comparer avec les 2 approches VLM (few-shot,
structured output — cf. extraction_vlm_fewshot.py / extraction_vlm_structured.py) sur
les mêmes documents prétraités (`*_final.md`).

Limite assumée : fr_core_news_sm est un modèle générique (labels CoNLL : PER, LOC, ORG,
MISC), pas entraîné sur le domaine AFAC. Il ne détectera pas "ARC 31" comme un Code ni
"GEDO" comme un System au sens de l'ontologie (ontology/afac_ontology.py). Cette approche
mesure donc un rappel de surface (quelles chaînes sont repérées comme entités, tous labels
génériques confondus), pas un accord de label avec les VLM — cf. extraction_schema.py.

Usage :
    uv run --active python neo4j_graphrag_ontology/graphrag/extraction_spacy.py --doc-name Mineur
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import spacy

THIS_DIR = Path(__file__).resolve().parent            # .../neo4j_graphrag_ontology/graphrag
KG_DIR = THIS_DIR.parent                                # .../neo4j_graphrag_ontology
PROJECT_ROOT = KG_DIR.parent                            # .../afac-preprocessing
for p in (str(PROJECT_ROOT), str(KG_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from extraction_schema import ExtractedEntity, ExtractionResult  # noqa: E402
from extraction_vlm_common import DEFAULT_OUTPUT_DIR, extraction_json_path, resolve_final_md  # noqa: E402

SPACY_MODEL = "fr_core_news_sm"


def extract_entities(nlp: spacy.language.Language, text: str) -> list[ExtractedEntity]:
    doc = nlp(text)
    return [
        ExtractedEntity(text=ent.text, label=ent.label_, start_char=ent.start_char, end_char=ent.end_char)
        for ent in doc.ents
    ]


def run(doc_name: str, output_dir: Path) -> ExtractionResult:
    md_path = resolve_final_md(doc_name, output_dir)
    text = md_path.read_text(encoding="utf-8")

    nlp = spacy.load(SPACY_MODEL)
    entities = extract_entities(nlp, text)

    return ExtractionResult(
        doc_name=doc_name,
        method="spacy",
        model_name=SPACY_MODEL,
        char_count=len(text),
        entities=entities,
    )


def print_summary(result: ExtractionResult) -> None:
    counts: dict[str, int] = {}
    for ent in result.entities:
        counts[ent.label] = counts.get(ent.label, 0) + 1
    print(f"\n=== spaCy ({result.model_name}) — {result.doc_name} ({result.char_count} car.) ===")
    print(f"{len(result.entities)} entités détectées")
    for label, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {label:6} {n}")
    for ent in result.entities[:20]:
        print(f"  [{ent.label:6}] {ent.text!r} ({ent.start_char}-{ent.end_char})")
    if len(result.entities) > 20:
        print(f"  … et {len(result.entities) - 20} de plus")


def main() -> None:
    ap = argparse.ArgumentParser(description="Extraction NER spaCy sur un document AFAC prétraité.")
    ap.add_argument("--doc-name", default="Mineur", help="Nom du document (dossier dans output_files_preprocessing)")
    ap.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Dossier des sorties de prétraitement")
    args = ap.parse_args()

    result = run(args.doc_name, Path(args.output_dir))
    print_summary(result)

    out_path = extraction_json_path(args.doc_name, "spacy")
    result.to_json_file(out_path)
    print(f"\n✅ Résultat écrit dans {out_path}")


if __name__ == "__main__":
    main()
