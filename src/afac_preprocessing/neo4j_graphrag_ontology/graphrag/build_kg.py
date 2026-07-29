"""
build_kg.py — Construit le knowledge graph AFAC dans Neo4j à partir d'un document prétraité.

Étape 3-4 du plan (cf. neo4j_graphrag_ontology/README.md) : extrait les entités/relations d'un
`*_final.md` via le VLM interne (endpoint OpenAI-compatible) en respectant l'ontologie fermée
(ontology/afac_ontology.py), puis écrit nœuds et relations dans Neo4j.

On réutilise :
  - la config VLM/embedding du projet (utils.vlm_client : base_url réduite + CA système) ;
  - les identifiants Neo4j de .env.test (NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD).

Le pipeline neo4j-graphrag s'exécute en asynchrone : le LLM reçoit donc un httpx.AsyncClient
(avec la CA), l'embedder un httpx.Client synchrone.

Usage :
    uv run --active python neo4j_graphrag_ontology/graphrag/build_kg.py --dotenv .env.test --doc-name Mineur
    uv run --active python neo4j_graphrag_ontology/graphrag/build_kg.py --doc-name Mineur --wipe
    uv run --active python neo4j_graphrag_ontology/graphrag/build_kg.py --doc-name Mineur --no-embeddings
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

import httpx
import neo4j
from neo4j_graphrag.experimental.components.types import LexicalGraphConfig
from neo4j_graphrag.experimental.pipeline.kg_builder import SimpleKGPipeline
from neo4j_graphrag.llm import OpenAILLM

# --- chemins projet : rend importables `utils.*` (racine projet) et `ontology.*` (dossier KG)
THIS_DIR = Path(__file__).resolve().parent          # .../neo4j_graphrag_ontology/graphrag
KG_DIR = THIS_DIR.parent                              # .../neo4j_graphrag_ontology
PROJECT_ROOT = KG_DIR.parent                          # .../afac-preprocessing
for p in (str(PROJECT_ROOT), str(KG_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from ontology.afac_ontology import NODE_TYPES, RELATIONSHIP_TYPES, PATTERNS  # noqa: E402
from shared.kg_shared_utils import EmbedderFactory  # noqa: E402
from utils.vlm_client import build_vlm_config  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
_log = logging.getLogger("build_kg")

# extra_body identique au reste du pipeline (évite content=null sur Qwen3)
_ENABLE_THINKING_FALSE = {"chat_template_kwargs": {"enable_thinking": False}}
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "output_files_preprocessing"

# Labels de la couche lexicale de neo4j-graphrag (structure Document→Chunk propre à la
# librairie). On les renomme pour NE PAS entrer en collision avec le label métier "Document"
# de notre ontologie (cf. ontology/afac_ontology.py) : sinon :Document mélange les nœuds de
# plomberie (path="document.txt", type="inline_text") et les entités métier extraites.
LEXICAL_GRAPH_CONFIG = LexicalGraphConfig(
    document_node_label="TextSource",
    chunk_node_label="TextChunk",
)


def resolve_final_md(doc_ref: str, output_dir: Path) -> Path:
    """Chemin du markdown final d'un document (préfère _final.md).

    *doc_ref* est le chemin du dossier document relatif à output_dir
    (lot F1 : la sortie reproduit l'arborescence d'entrée) ; le nom du
    document en est le dernier segment."""
    doc_dir = output_dir / doc_ref
    doc_name = Path(doc_ref).name
    for suffix in ("_final.md", ".md"):
        candidate = doc_dir / f"{doc_name}{suffix}"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"Aucun markdown final trouvé pour « {doc_ref} » dans {doc_dir}")


def build_llm(cfg) -> OpenAILLM:
    """LLM d'extraction pointé sur le VLM interne (client async avec CA système)."""
    return OpenAILLM(
        model_name=cfg.vlm_model_name,
        model_params={"temperature": 0.0, "extra_body": _ENABLE_THINKING_FALSE},
        base_url=cfg.vlm_base_url,
        api_key="no-key",
        http_client=httpx.AsyncClient(verify=cfg.ca_path, timeout=180.0),
    )


def wipe_graph(driver: neo4j.Driver) -> None:
    _log.warning("Suppression de tout le contenu du graphe (--wipe)")
    driver.execute_query("MATCH (n) DETACH DELETE n")


def strip_internal_labels(driver: neo4j.Driver) -> None:
    """Retire les labels internes __KGBuilder__/__Entity__ posés par neo4j-graphrag sur
    chaque nœud (en plus du label métier). Neo4j Browser colore un nœud multi-labels selon
    l'ID de label interne le plus ancien, et ces deux labels techniques sont toujours créés
    avant le label métier — ils gagnent donc systématiquement le filtre couleur si on les
    laisse. Sans effet sur normalize_pass() (qui exclut déjà les labels préfixés __)."""
    driver.execute_query("MATCH (n) REMOVE n:__KGBuilder__:__Entity__")


def graph_summary(driver: neo4j.Driver) -> None:
    """Affiche un résumé de ce qui a été chargé (compte par label et par type de relation)."""
    nodes, _, _ = driver.execute_query(
        "MATCH (n) UNWIND labels(n) AS l RETURN l AS label, count(*) AS n ORDER BY n DESC"
    )
    rels, _, _ = driver.execute_query(
        "MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS n ORDER BY n DESC"
    )
    print("\n=== Nœuds par label ===")
    for rec in nodes:
        print(f"  {rec['label']:12} {rec['n']}")
    print("=== Relations par type ===")
    for rec in rels:
        print(f"  {rec['type']:12} {rec['n']}")
    # Aperçu de quelques triplets métier (hors nœuds techniques Chunk/Document lexical)
    sample, _, _ = driver.execute_query(
        """
        MATCH (a)-[r]->(b)
        WHERE a.name IS NOT NULL AND b.name IS NOT NULL
        RETURN a.name AS src, type(r) AS rel, b.name AS dst
        LIMIT 15
        """
    )
    if sample:
        print("=== Exemples de triplets ===")
        for rec in sample:
            print(f"  ({rec['src']}) -[:{rec['rel']}]-> ({rec['dst']})")


async def run(doc_name: str, dotenv: str | None, output_dir: Path, use_embeddings: bool, wipe: bool) -> None:
    cfg = build_vlm_config(Path(dotenv) if dotenv else None)  # charge aussi .env dans os.environ

    md_path = resolve_final_md(doc_name, output_dir)
    text = md_path.read_text(encoding="utf-8")
    _log.info("Document : %s (%d caractères)", md_path.name, len(text))

    uri = os.environ.get("NEO4J_URI") or "bolt://localhost:7687"
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "")
    driver = neo4j.GraphDatabase.driver(uri, auth=(user, password))
    driver.verify_connectivity()
    _log.info("Neo4j joignable : %s", uri)

    if wipe:
        wipe_graph(driver)

    llm = build_llm(cfg)
    embedder = EmbedderFactory(cfg).build() if use_embeddings else None
    if not use_embeddings:
        _log.info("Embeddings désactivés (--no-embeddings)")

    pipeline = SimpleKGPipeline(
        llm=llm,
        driver=driver,
        embedder=embedder,
        entities=NODE_TYPES,
        relations=RELATIONSHIP_TYPES,
        potential_schema=PATTERNS,
        from_file=False,  # on fournit du texte brut (run_async(text=...)), pas de loader de fichier
        perform_entity_resolution=True,
        lexical_graph_config=LEXICAL_GRAPH_CONFIG,
        neo4j_database=None,
    )

    _log.info("Extraction + chargement du graphe en cours…")
    result = await pipeline.run_async(text=text)
    _log.info("Pipeline terminé : %s", getattr(result, "result", result))

    strip_internal_labels(driver)
    graph_summary(driver)
    driver.close()
    print(f"\n✅ Graphe construit pour « {doc_name} ». Ouvrir http://localhost:7474 pour explorer.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Construit le KG AFAC dans Neo4j depuis un document prétraité.")
    ap.add_argument("--doc-name", default="Mineur", help="Nom du document (dossier dans output_files_preprocessing)")
    ap.add_argument("--dotenv", default=".env.test", help="Fichier .env à charger (défaut : .env.test)")
    ap.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Dossier des sorties de prétraitement")
    ap.add_argument("--no-embeddings", dest="embeddings", action="store_false", help="Ne pas générer d'embeddings de chunks")
    ap.add_argument("--wipe", action="store_true", help="Vider le graphe avant chargement")
    args = ap.parse_args()

    asyncio.run(run(
        doc_name=args.doc_name,
        dotenv=args.dotenv,
        output_dir=Path(args.output_dir),
        use_embeddings=args.embeddings,
        wipe=args.wipe,
    ))


if __name__ == "__main__":
    main()
