"""
kg_shared_utils.py — Utilitaires partagés entre les deux générations du pipeline KG AFAC :
`graphrag/` (ontologie fermée, SimpleKGPipeline) et `extraction_concepts/` (concept-guided,
prédicats libres).

Sans ce module, `extraction_concepts/build_kg_from_concepts.py` allait chercher ces deux
fonctions directement dans `graphrag/build_kg.py` / `graphrag/batch_build_kg.py` — un sens de
dépendance à l'envers (le nouveau pipeline dépendant de fichiers propres à l'ancien) qui forçait
en plus un hack de `sys.path` fragile (ajouter `graphrag/` lui-même au path pour que l'import nu
`from build_kg import ...` de `batch_build_kg.py` résolve). Les deux pipelines importent
maintenant ce module neutre ; `graphrag/build_kg.py` et `graphrag/batch_build_kg.py` continuent
de fonctionner à l'identique via un ré-export.
"""
from __future__ import annotations

import logging
import sys
from collections import defaultdict
from pathlib import Path

import httpx
import neo4j
from neo4j_graphrag.embeddings import OpenAIEmbeddings

THIS_DIR = Path(__file__).resolve().parent          # .../neo4j_graphrag_ontology/shared
KG_DIR = THIS_DIR.parent                              # .../neo4j_graphrag_ontology
PROJECT_ROOT = KG_DIR.parent                          # .../afac-preprocessing
for p in (str(PROJECT_ROOT), str(KG_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from ontology.afac_ontology import normalize_name  # noqa: E402

_log = logging.getLogger("kg_shared_utils")


class EmbedderFactory:
    """Construit l'embedder officiel (neo4j_graphrag.embeddings.OpenAIEmbeddings) pointé sur
    l'endpoint d'embedding interne du projet (CA système, base_url réduite) — même client que
    partout ailleurs dans le pipeline. Une classe plutôt qu'une fonction nue : `cfg` est fixé
    une fois à la construction, interchangeable avec un autre provider/modèle sans toucher aux
    appelants (qui n'interagissent qu'avec `.build()`)."""

    def __init__(self, cfg) -> None:
        self.cfg = cfg

    def build(self) -> OpenAIEmbeddings:
        return OpenAIEmbeddings(
            model=self.cfg.embedding_model_name,
            base_url=self.cfg.embedding_base_url,
            api_key="no-key",
            http_client=httpx.Client(verify=self.cfg.ca_path, timeout=120.0),
        )


class GraphNormalizer:
    """Fusionne les nœuds métier dont le nom se rabat sur la même forme canonique.

    Groupe par (labels métier, normalize_name(name)) ; pour chaque groupe de >1 nœud, fusionne
    via apoc.refactor.mergeNodes (combine propriétés, fusionne relations dupliquées) et fixe le
    nom canonique. Pour un nœud seul dont le nom diffère de sa forme canonique, met juste à jour.
    """

    def __init__(self, driver: neo4j.Driver) -> None:
        self.driver = driver

    def run(self) -> int:
        records, _, _ = self.driver.execute_query(
            """
            MATCH (n) WHERE n.name IS NOT NULL
            RETURN elementId(n) AS id, n.name AS name,
                   [l IN labels(n) WHERE NOT l STARTS WITH '__'] AS labels
            """
        )
        groups: dict[tuple, list[str]] = defaultdict(list)
        for r in records:
            canonical = normalize_name(r["name"])
            key = (tuple(sorted(r["labels"])), canonical)
            groups[key].append(r["id"])

        merged, renamed = 0, 0
        for (labels, canonical), ids in groups.items():
            if len(ids) > 1:
                self.driver.execute_query(
                    """
                    MATCH (n) WHERE elementId(n) IN $ids
                    WITH collect(n) AS ns
                    CALL apoc.refactor.mergeNodes(ns, {properties:'combine', mergeRels:true})
                    YIELD node SET node.name = $canonical
                    RETURN elementId(node)
                    """,
                    ids=ids, canonical=canonical,
                )
                merged += len(ids) - 1
            else:
                self.driver.execute_query(
                    "MATCH (n) WHERE elementId(n) = $id AND n.name <> $canonical SET n.name = $canonical",
                    id=ids[0], canonical=canonical,
                )
                renamed += 1  # compte les candidats ; SET no-op si déjà canonique
        _log.info("Normalisation : %d nœuds fusionnés, %d nom(s) candidat(s) à un renommage "
                   "(no-op si déjà canonique)", merged, renamed)
        return merged
