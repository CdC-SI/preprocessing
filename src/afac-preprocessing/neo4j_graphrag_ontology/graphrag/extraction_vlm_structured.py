"""
extraction_vlm_structured.py — Approche 3/3 : extraction d'entités + relations via VLM avec
sortie structurée (response_format Pydantic, cf. utils.vlm_client.text_completion_structured).
Zéro exemple dans le prompt (contrairement à extraction_vlm_fewshot.py) : c'est la contrainte
de schéma côté API, pas le prompt, qui garantit un JSON conforme.

Ontologie ouverte, comme l'approche few-shot : le VLM choisit lui-même le label de chaque
entité et le type de chaque relation, sans liste fermée imposée (cf. extraction_schema.py).

Usage :
    uv run --active python neo4j_graphrag_ontology/graphrag/extraction_vlm_structured.py --doc-name Mineur
    uv run --active python neo4j_graphrag_ontology/graphrag/extraction_vlm_structured.py --doc-name Mineur --dotenv .env.test
"""
from __future__ import annotations

import argparse
from pathlib import Path

from extraction_schema import ExtractedEntity, ExtractedRelation, ExtractionResult, VlmExtractionOutput, dedupe_entities_and_relations
from extraction_vlm_common import DEFAULT_OUTPUT_DIR, DOMAIN_CONTEXT, chunk_text, extraction_json_path, resolve_final_md
from utils.vlm_client import build_sync_client, build_vlm_config, text_completion_structured


def build_system_prompt() -> str:
    return f"""Tu extrais les entités nommées et les relations entre elles à partir d'un \
document. {DOMAIN_CONTEXT}

Choisis toi-même le label de chaque entité et le type de chaque relation : il n'y a pas de \
liste fermée à respecter. Utilise des labels courts, cohérents entre eux (ne renomme pas la \
même notion différemment d'une entité à l'autre), et en français.

Pour chaque relation, `source` et `target` doivent reprendre exactement le texte d'une \
entité extraite.
"""


def run(doc_name: str, dotenv: str | None, output_dir: Path) -> ExtractionResult:
    cfg = build_vlm_config(Path(dotenv) if dotenv else None)
    client = build_sync_client(cfg)

    md_path = resolve_final_md(doc_name, output_dir)
    text = md_path.read_text(encoding="utf-8")

    system_prompt = build_system_prompt()
    all_entities: list[ExtractedEntity] = []
    all_relations: list[ExtractedRelation] = []
    for chunk in chunk_text(text):
        parsed: VlmExtractionOutput = text_completion_structured(
            client, cfg.vlm_model_name, system_prompt, chunk, response_format=VlmExtractionOutput
        )
        all_entities += parsed.entities
        all_relations += parsed.relations
    entities, relations = dedupe_entities_and_relations(all_entities, all_relations)

    return ExtractionResult(
        doc_name=doc_name,
        method="vlm_structured",
        model_name=cfg.vlm_model_name,
        char_count=len(text),
        entities=entities,
        relations=relations,
    )


def print_summary(result: ExtractionResult) -> None:
    print(f"\n=== VLM structured output ({result.model_name}) — {result.doc_name} ({result.char_count} car.) ===")
    print(f"{len(result.entities)} entités, {len(result.relations)} relations")
    for ent in result.entities:
        print(f"  [{ent.label}] {ent.text!r}")
    for rel in result.relations:
        print(f"  ({rel.source}) -[{rel.relation}]-> ({rel.target})")


def main() -> None:
    ap = argparse.ArgumentParser(description="Extraction d'entités + relations VLM (structured output) sur un document AFAC.")
    ap.add_argument("--doc-name", default="Mineur")
    ap.add_argument("--dotenv", default=".env.test")
    ap.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    args = ap.parse_args()

    result = run(args.doc_name, args.dotenv, Path(args.output_dir))
    print_summary(result)

    out_path = extraction_json_path(args.doc_name, "vlm_structured")
    result.to_json_file(out_path)
    print(f"\n✅ Résultat écrit dans {out_path}")


if __name__ == "__main__":
    main()