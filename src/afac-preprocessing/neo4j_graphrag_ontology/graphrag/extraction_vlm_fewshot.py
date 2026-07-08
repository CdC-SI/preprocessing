"""
extraction_vlm_fewshot.py — Approche 2/3 : extraction d'entités + relations via VLM guidé
par few-shot prompting (des exemples input → JSON dans le prompt), sans contrainte de
schéma côté API et SANS ontologie fermée : le VLM choisit lui-même le label de chaque
entité et le type de chaque relation (cf. extraction_schema.py — l'ontologie n'est pas
encore stabilisée, on observe donc le vocabulaire spontané du modèle).

Le modèle peut, en théorie, dévier du format JSON demandé — c'est justement ce qu'on
compare à extraction_vlm_structured.py (approche 3, schéma imposé côté API).

Usage :
    uv run --active python neo4j_graphrag_ontology/graphrag/extraction_vlm_fewshot.py --doc-name Mineur
    uv run --active python neo4j_graphrag_ontology/graphrag/extraction_vlm_fewshot.py --doc-name Mineur --dotenv .env.test
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from extraction_schema import ExtractedEntity, ExtractedRelation, ExtractionResult, dedupe_entities_and_relations
from extraction_vlm_common import (
    DEFAULT_OUTPUT_DIR,
    DOMAIN_CONTEXT,
    chunk_text,
    extract_json_object,
    extraction_json_path,
    resolve_final_md,
)
from utils.vlm_client import build_sync_client, build_vlm_config, text_completion

# Deux exemples illustratifs (pas issus d'un vrai document, domaine volontairement différent
# pour ne pas laisser penser que ces labels précis sont la liste attendue) : ils montrent le
# FORMAT (texte/label libre, relations avec un type libre), pas un vocabulaire à respecter.
_FEW_SHOT_EXAMPLES = [
    (
        "Pour emprunter un livre, l'usager doit présenter sa carte de bibliothèque. Le prêt "
        "standard dure 3 semaines et peut être renouvelé une fois via le portail en ligne.",
        {
            "entities": [
                {"text": "carte de bibliothèque", "label": "Document d'identification"},
                {"text": "prêt standard", "label": "Type de service"},
                {"text": "3 semaines", "label": "Durée"},
                {"text": "portail en ligne", "label": "Outil numérique"},
            ],
            "relations": [
                {"source": "prêt standard", "relation": "nécessite", "target": "carte de bibliothèque"},
                {"source": "prêt standard", "relation": "a pour durée", "target": "3 semaines"},
                {"source": "prêt standard", "relation": "renouvelable via", "target": "portail en ligne"},
            ],
        },
    ),
    (
        "Le capitaine doit valider le plan de vol avant le décollage. En cas de météo "
        "défavorable, la tour de contrôle peut imposer un report.",
        {
            "entities": [
                {"text": "capitaine", "label": "Rôle"},
                {"text": "plan de vol", "label": "Document opérationnel"},
                {"text": "décollage", "label": "Étape de procédure"},
                {"text": "météo défavorable", "label": "Condition"},
                {"text": "tour de contrôle", "label": "Autorité"},
                {"text": "report", "label": "Action"},
            ],
            "relations": [
                {"source": "capitaine", "relation": "valide", "target": "plan de vol"},
                {"source": "plan de vol", "relation": "précède", "target": "décollage"},
                {"source": "météo défavorable", "relation": "déclenche", "target": "report"},
                {"source": "tour de contrôle", "relation": "impose", "target": "report"},
            ],
        },
    ),
]


def build_system_prompt() -> str:
    examples_block = "\n\n".join(
        f"Texte :\n{text}\n\nJSON attendu :\n{json.dumps(expected, ensure_ascii=False, indent=2)}"
        for text, expected in _FEW_SHOT_EXAMPLES
    )
    return f"""Tu extrais les entités nommées et les relations entre elles à partir d'un \
document. {DOMAIN_CONTEXT}

Choisis toi-même le label de chaque entité et le type de chaque relation : il n'y a pas de \
liste fermée à respecter. Utilise des labels courts, cohérents entre eux (ne renomme pas la \
même notion différemment d'une entité à l'autre), et en français.

Retourne un objet JSON de la forme :
{{"entities": [{{"text": "...", "label": "..."}}], "relations": [{{"source": "...", "relation": "...", "target": "..."}}]}}

`source` et `target` doivent reprendre exactement le texte d'une entité listée dans "entities".
N'entoure pas le JSON de backticks. Ne renvoie que le JSON, rien d'autre.

Les 2 exemples ci-dessous illustrent uniquement le FORMAT attendu, sur un domaine différent —
n'en réutilise pas les labels, choisis ceux qui conviennent au texte que tu analyses :

{examples_block}
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
        raw = text_completion(client, cfg.vlm_model_name, system_prompt, chunk)
        parsed = extract_json_object(raw)
        all_entities += [ExtractedEntity(text=e["text"], label=e["label"]) for e in parsed.get("entities", [])]
        all_relations += [
            ExtractedRelation(source=r["source"], target=r["target"], relation=r["relation"])
            for r in parsed.get("relations", [])
        ]
    entities, relations = dedupe_entities_and_relations(all_entities, all_relations)

    return ExtractionResult(
        doc_name=doc_name,
        method="vlm_fewshot",
        model_name=cfg.vlm_model_name,
        char_count=len(text),
        entities=entities,
        relations=relations,
    )


def print_summary(result: ExtractionResult) -> None:
    print(f"\n=== VLM few-shot ({result.model_name}) — {result.doc_name} ({result.char_count} car.) ===")
    print(f"{len(result.entities)} entités, {len(result.relations)} relations")
    for ent in result.entities:
        print(f"  [{ent.label}] {ent.text!r}")
    for rel in result.relations:
        print(f"  ({rel.source}) -[{rel.relation}]-> ({rel.target})")


def main() -> None:
    ap = argparse.ArgumentParser(description="Extraction d'entités + relations VLM (few-shot prompting) sur un document AFAC.")
    ap.add_argument("--doc-name", default="Mineur")
    ap.add_argument("--dotenv", default=".env.test")
    ap.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    args = ap.parse_args()

    result = run(args.doc_name, args.dotenv, Path(args.output_dir))
    print_summary(result)

    out_path = extraction_json_path(args.doc_name, "vlm_fewshot")
    result.to_json_file(out_path)
    print(f"\n✅ Résultat écrit dans {out_path}")


if __name__ == "__main__":
    main()