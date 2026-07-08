"""
extraction_vlm_common.py — Éléments partagés par les 2 approches VLM (few-shot / structured
output) : chargement du document, contexte de domaine (SANS liste de labels fermée — cf.
extraction_schema.py, l'ontologie n'est pas encore stabilisée), parsing JSON tolérant pour
l'approche few-shot (le modèle peut entourer le JSON de ```json ... ``` malgré la consigne
contraire).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent            # .../neo4j_graphrag_ontology/graphrag
KG_DIR = THIS_DIR.parent                                # .../neo4j_graphrag_ontology
PROJECT_ROOT = KG_DIR.parent                            # .../afac-preprocessing
for p in (str(PROJECT_ROOT), str(KG_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "output_files_preprocessing"
EXAMPLES_DIR = KG_DIR / "examples"
ALL_METHODS = ("spacy", "vlm_fewshot", "vlm_structured")

# Même ordre de grandeur que le FixedSizeSplitter par défaut de neo4j-graphrag (build_kg.py) —
# la seule valeur de ce type déjà validée contre ce VLM dans ce projet. Nécessaire ici : sans
# découpage, un document volumineux (liste de pays, tableau de représentations...) envoyé en un
# seul appel fait dépasser le timeout fixe du gateway VLM (504 Gateway Time-out).
CHUNK_SIZE = 4000
CHUNK_OVERLAP = 200

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)

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


def list_documents(output_dir: Path = DEFAULT_OUTPUT_DIR) -> list[str]:
    """Noms des documents ayant un markdown final, triés — même logique que batch_build_kg.py."""
    return [d.name for d in sorted(output_dir.iterdir()) if d.is_dir() and (d / f"{d.name}_final.md").exists()]


def resolve_final_md(doc_name: str, output_dir: Path = DEFAULT_OUTPUT_DIR) -> Path:
    """Chemin du markdown final d'un document (préfère _final.md) — même logique que build_kg.py."""
    doc_dir = output_dir / doc_name
    for suffix in ("_final.md", ".md"):
        candidate = doc_dir / f"{doc_name}{suffix}"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"Aucun markdown final trouvé pour « {doc_name} » dans {doc_dir}")


def method_dir(method: str) -> Path:
    """Sous-dossier dédié à une méthode dans examples/ — regroupe tous les documents d'une
    même méthode ensemble (lisibilité : 20 docs x 3 méthodes x 2 formats à plat serait
    illisible). ex. examples/spacy/, examples/vlm_structured/."""
    d = EXAMPLES_DIR / method
    d.mkdir(parents=True, exist_ok=True)
    return d


def extraction_json_path(doc_name: str, method: str) -> Path:
    return method_dir(method) / f"{doc_name}.json"


def extraction_graph_path(doc_name: str, method: str) -> Path:
    return method_dir(method) / f"{doc_name}_graph.html"


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Découpe `text` en morceaux d'environ `chunk_size` caractères, avec chevauchement.
    Cale la coupure sur le dernier saut de ligne de la fenêtre quand c'est possible, pour ne
    pas trancher une ligne de tableau ou une entité en deux. Documents courts (le cas le plus
    fréquent, ex. Mineur) : retourne [text] tel quel, un seul appel VLM comme avant."""
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start, n = 0, len(text)
    while start < n:
        end = min(start + chunk_size, n)
        if end < n:
            newline = text.rfind("\n", start, end)
            if newline > start:
                end = newline
        chunks.append(text[start:end])
        if end >= n:
            break
        start = max(end - overlap, start + 1)  # garantit une progression même sur une coupure courte
    return chunks


def extract_json_object(raw: str) -> dict:
    """Parse la réponse VLM en JSON, en tolérant un bloc ```json ... ``` autour (few-shot
    n'a pas de garantie de format contrairement à l'approche structured output)."""
    fenced = _JSON_FENCE_RE.search(raw)
    candidate = fenced.group(1) if fenced else raw
    return json.loads(candidate)
