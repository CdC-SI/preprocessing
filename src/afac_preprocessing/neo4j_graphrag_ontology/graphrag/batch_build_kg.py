"""
batch_build_kg.py — Construit le knowledge graph AFAC pour TOUS les documents d'un dossier.

Étape 1 du plan post-POC (cf. README) : charge les 20 aide-mémoire du thème Adhésion dans un
seul graphe Neo4j, sans vider entre chaque document, pour que les entités partagées (GEDO,
ARC 61, SITAX…) relient les documents entre eux. Après chargement, applique une passe de
normalisation (ontology.normalize_name + apoc.refactor.mergeNodes) pour fusionner les variantes
de casse/orthographe que le résolveur intégré (match exact name+label) laisse passer
(ex. TeleZas / TeleZas3, ARA / ara).

Usage :
    uv run --active python neo4j_graphrag_ontology/graphrag/batch_build_kg.py --dotenv .env.test
    uv run --active python neo4j_graphrag_ontology/graphrag/batch_build_kg.py --no-wipe --no-embeddings
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

import neo4j
from neo4j_graphrag.experimental.pipeline.kg_builder import SimpleKGPipeline

# Réutilise la config/paths/helpers du script unitaire (met aussi sys.path en place).
from build_kg import (
    DEFAULT_OUTPUT_DIR,
    LEXICAL_GRAPH_CONFIG,
    build_llm,
    graph_summary,
    resolve_final_md,
    strip_internal_labels,
)
from ontology.afac_ontology import NODE_TYPES, PATTERNS, RELATIONSHIP_TYPES
from shared.kg_shared_utils import EmbedderFactory, GraphNormalizer
from utils.vlm_client import build_vlm_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
_log = logging.getLogger("batch_build_kg")


def list_documents(output_dir: Path) -> list[str]:
    """Documents ayant un markdown final, triés.

    Renvoie des chemins RELATIFS à output_dir (lot F1) : la sortie reproduit
    l'arborescence d'entrée, et deux documents homonymes rangés dans des
    dossiers différents sont désormais deux entrées distinctes."""
    docs = []
    for d in sorted(output_dir.rglob("*")):
        if d.is_dir() and (d / f"{d.name}_final.md").exists():
            docs.append(str(d.relative_to(output_dir)))
    return docs


async def run(dotenv: str | None, output_dir: Path, use_embeddings: bool, wipe: bool) -> None:
    cfg = build_vlm_config(Path(dotenv) if dotenv else None)
    import os
    uri = os.environ.get("NEO4J_URI") or "bolt://localhost:7687"
    driver = neo4j.GraphDatabase.driver(
        uri, auth=(os.environ.get("NEO4J_USER", "neo4j"), os.environ.get("NEO4J_PASSWORD", "")),
    )
    driver.verify_connectivity()
    _log.info("Neo4j joignable : %s", uri)

    if wipe:
        _log.warning("Vidage du graphe avant chargement (--wipe)")
        driver.execute_query("MATCH (n) DETACH DELETE n")

    docs = list_documents(output_dir)
    _log.info("%d documents à charger : %s", len(docs), ", ".join(docs))

    llm = build_llm(cfg)
    embedder = EmbedderFactory(cfg).build() if use_embeddings else None
    pipeline = SimpleKGPipeline(
        llm=llm, driver=driver, embedder=embedder,
        entities=NODE_TYPES, relations=RELATIONSHIP_TYPES, potential_schema=PATTERNS,
        from_file=False, perform_entity_resolution=True,
        lexical_graph_config=LEXICAL_GRAPH_CONFIG,
    )

    ok, failed = 0, []
    for i, doc in enumerate(docs, 1):
        try:
            text = resolve_final_md(doc, output_dir).read_text(encoding="utf-8")
            _log.info("[%d/%d] %s (%d car.)", i, len(docs), doc, len(text))
            await pipeline.run_async(text=text)
            ok += 1
        except Exception as exc:  # noqa: BLE001 — on continue malgré l'échec d'un document
            logging.exception("[%d/%d] ÉCHEC %s : %s", i, len(docs), doc, exc)
            failed.append(doc)

    _log.info("Chargement terminé : %d ok, %d échecs", ok, len(failed))
    if failed:
        _log.warning("Documents en échec : %s", ", ".join(failed))

    strip_internal_labels(driver)

    _log.info("Passe de normalisation des noms d'entités…")
    GraphNormalizer(driver).run()

    graph_summary(driver)
    driver.close()
    print(f"\nGraphe construit pour {ok}/{len(docs)} documents. Ouvrir http://localhost:7474.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Construit le KG AFAC pour tous les documents d'un dossier.")
    ap.add_argument("--dotenv", default=".env.test")
    ap.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    ap.add_argument("--no-embeddings", dest="embeddings", action="store_false")
    ap.add_argument("--no-wipe", dest="wipe", action="store_false", help="Ne pas vider le graphe d'abord")
    args = ap.parse_args()
    asyncio.run(run(args.dotenv, Path(args.output_dir), args.embeddings, args.wipe))


if __name__ == "__main__":
    main()
