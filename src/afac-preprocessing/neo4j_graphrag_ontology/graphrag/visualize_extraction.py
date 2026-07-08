"""
visualize_extraction.py — Rendu graphe interactif (PyVis) d'une extraction produite par
extraction_spacy.py / extraction_vlm_fewshot.py / extraction_vlm_structured.py.

Un nœud par entité, une arête par relation. spaCy ne produit pas de relations : son rendu
est donc un nuage de nœuds isolés (attendu — c'est un NER générique, pas un extracteur de
relations). Les 2 approches VLM produisent un vrai graphe.

Même logique que graph_url.py pour la taille des nœuds : proportionnelle au degré, pour
repérer visuellement les entités les plus connectées.

Usage :
    uv run python neo4j_graphrag_ontology/graphrag/visualize_extraction.py --doc-name Mineur
    uv run python neo4j_graphrag_ontology/graphrag/visualize_extraction.py --doc-name Mineur --method vlm_structured
"""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
import networkx as nx
from pyvis.network import Network

from compare_extractions import load
from extraction_schema import ExtractionResult
from extraction_vlm_common import ALL_METHODS, extraction_graph_path


def build_label_colors(labels: set[str]) -> dict[str, str]:
    """Couleur déterministe par label (vocabulaire ouvert, donc pas de palette fixe comme
    dans graph_url.py — on en génère une à partir du nombre de labels distincts observés)."""
    ordered = sorted(labels)
    cmap = matplotlib.colormaps["tab20"].resampled(max(len(ordered), 1))
    return {label: matplotlib.colors.rgb2hex(cmap(i)) for i, label in enumerate(ordered)}


def build_graph(result: ExtractionResult) -> nx.DiGraph:
    graph = nx.DiGraph()
    for ent in result.entities:
        graph.add_node(ent.text, label=ent.label)
    for rel in result.relations:
        # Défensif : le VLM peut référencer un texte légèrement différent de l'entité
        # extraite (ex. reformulation). On crée le nœud manquant plutôt que de perdre l'arête.
        if rel.source not in graph:
            graph.add_node(rel.source, label="?")
        if rel.target not in graph:
            graph.add_node(rel.target, label="?")
        graph.add_edge(rel.source, rel.target, relation=rel.relation)
    return graph


def render(result: ExtractionResult, out_path: Path) -> None:
    graph = build_graph(result)
    colors = build_label_colors({d["label"] for _, d in graph.nodes(data=True)})
    degrees = dict(graph.degree())

    # cdn_resources="remote" : évite que pyvis n'écrive un dossier lib/ (vis-network,
    # tom-select) relatif au répertoire courant du process plutôt qu'au fichier HTML
    # généré — ce qui cassait le rendu en ouvrant le HTML depuis un autre dossier.
    net = Network(notebook=False, height="900px", width="100%", bgcolor="#FFFFFF",
                  font_color="black", directed=True, cdn_resources="remote")
    for node, data in graph.nodes(data=True):
        label = data["label"]
        net.add_node(
            node, label=node, title=f"{label}\n{node}",
            color=colors[label], size=15 + 6 * degrees[node],
        )
    for u, v, data in graph.edges(data=True):
        net.add_edge(u, v, label=data["relation"], title=data["relation"])

    net.write_html(str(out_path), notebook=False, open_browser=False)


def main() -> None:
    ap = argparse.ArgumentParser(description="Visualisation graphe (PyVis) d'une extraction.")
    ap.add_argument("--doc-name", default="Mineur")
    ap.add_argument("--method", choices=(*ALL_METHODS, "all"), default="all")
    args = ap.parse_args()

    methods = ALL_METHODS if args.method == "all" else (args.method,)
    for method in methods:
        result = load(args.doc_name, method)
        if result is None:
            continue
        out_path = extraction_graph_path(args.doc_name, method)
        render(result, out_path)
        n_isolated = sum(1 for _, d in build_graph(result).degree() if d == 0)
        print(f"✅ {method:14} → {out_path}  ({len(result.entities)} entités, "
              f"{len(result.relations)} relations, {n_isolated} nœud(s) isolé(s))")


if __name__ == "__main__":
    main()
