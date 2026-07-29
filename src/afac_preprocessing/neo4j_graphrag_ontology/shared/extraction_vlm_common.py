"""
extraction_vlm_common.py — Utilitaires partagés par le pipeline KG AFAC (chargement de
document, découpage en chunks, parsing JSON tolérant, contexte de domaine pour le VLM).

Consommé par `extraction_concepts/*` (concept-guided) — le cluster qui consommait aussi
`method_dir`/`extraction_json_path`/`extraction_graph_path`/`EXAMPLES_DIR`/`ALL_METHODS` (POC de
comparaison spaCy / VLM few-shot / VLM structured) a été archivé dans `trash/graphrag/` ; ces
éléments, devenus du code mort, ont été retirés de ce module au passage.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent            # .../neo4j_graphrag_ontology/shared
KG_DIR = THIS_DIR.parent                                # .../neo4j_graphrag_ontology
PROJECT_ROOT = KG_DIR.parent                            # .../afac-preprocessing
for p in (str(PROJECT_ROOT), str(KG_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "output_files_preprocessing"

# Même ordre de grandeur que le FixedSizeSplitter par défaut de neo4j-graphrag (build_kg.py) —
# la seule valeur de ce type déjà validée contre ce VLM dans ce projet. Nécessaire ici : sans
# découpage, un document volumineux (liste de pays, tableau de représentations...) envoyé en un
# seul appel fait dépasser le timeout fixe du gateway VLM (504 Gateway Time-out).
CHUNK_SIZE = 4000
CHUNK_OVERLAP = 200

# Contexte de domaine donné au VLM, volontairement sans liste de labels fermée : le but de
# cette passe d'extraction est justement d'observer le vocabulaire (types d'entités, types
# de relations) que le modèle choisit spontanément, pour nourrir l'ontologie plus tard.
DOMAIN_CONTEXT = (
    "Le document ci-dessous fait partie d'un corpus d'aide-mémoires opérationnels pour "
    "l'AFAC (assurance facultative AVS/AI, gérée par la Centrale de compensation suisse). "
    "On y trouve typiquement des systèmes informatiques métier, des codes d'action ou "
    "motifs codifiés, des statuts et notions d'assurance, des conditions d'éligibilité, des "
    "références légales et des liens vers des processus métier — mais cette liste n'est pas "
    "exhaustive ni imposée."
)


class DocumentLocator:
    """Résout les documents prétraités (markdown final) d'un dossier de sortie. Encapsulé dans
    une classe (plutôt que deux fonctions nues prenant `output_dir` à chaque appel) : `output_dir`
    est fixé une fois à la construction — c'est déjà comme ça que tous les appelants l'utilisent
    (`self.output_dir` stocké une fois), la classe le rend juste explicite."""

    def __init__(self, output_dir: Path = DEFAULT_OUTPUT_DIR) -> None:
        self.output_dir = output_dir

    def list_documents(self) -> list[str]:
        """Documents ayant un markdown final, triés.

        Renvoie des chemins RELATIFS à output_dir (lot F1) : la sortie
        reproduit l'arborescence d'entrée."""
        return [
            str(d.relative_to(self.output_dir))
            for d in sorted(self.output_dir.rglob("*"))
            if d.is_dir() and (d / f"{d.name}_final.md").exists()
        ]

    def resolve_final_md(self, doc_ref: str) -> Path:
        """Chemin du markdown final d'un document (préfère `_final.md`).

        *doc_ref* est un chemin relatif à output_dir (cf. list_documents)."""
        doc_dir = self.output_dir / doc_ref
        doc_name = Path(doc_ref).name
        for suffix in ("_final.md", ".md"):
            candidate = doc_dir / f"{doc_name}{suffix}"
            if candidate.exists():
                return candidate
        raise FileNotFoundError(f"Aucun markdown final trouvé pour « {doc_ref} » dans {doc_dir}")


class TextChunker:
    """Découpe un texte en morceaux d'environ `chunk_size` caractères, avec chevauchement.
    Cale la coupure sur le dernier saut de ligne de la fenêtre quand c'est possible, pour ne
    pas trancher une ligne de tableau ou une entité en deux. Documents courts (le cas le plus
    fréquent, ex. Mineur) : retourne [text] tel quel, un seul appel VLM comme avant.

    Une classe plutôt qu'une fonction nue : `chunk_size`/`overlap` se règlent une fois à la
    construction, interchangeable avec une autre stratégie de découpage sans toucher aux
    appelants (qui n'interagissent qu'avec `.split()`)."""

    def __init__(self, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> None:
        self.chunk_size = chunk_size
        self.overlap = overlap

    def split(self, text: str) -> list[str]:
        if len(text) <= self.chunk_size:
            return [text]

        chunks = []
        start, n = 0, len(text)
        while start < n:
            end = min(start + self.chunk_size, n)
            if end < n:
                newline = text.rfind("\n", start, end)
                if newline > start:
                    end = newline
            chunks.append(text[start:end])
            if end >= n:
                break
            start = max(end - self.overlap, start + 1)  # garantit une progression même sur une coupure courte
        return chunks


class TolerantJsonParser:
    """Parse la réponse VLM en JSON, en tolérant un bloc ```json ... ``` autour (few-shot n'a
    pas de garantie de format contrairement à l'approche structured output)."""

    _FENCE_RE = re.compile(r"```(?:json)?(.*?)```", re.DOTALL)

    def parse(self, raw: str) -> dict:
        fenced = self._FENCE_RE.search(raw)
        candidate = fenced.group(1).strip() if fenced else raw
        return json.loads(candidate)
