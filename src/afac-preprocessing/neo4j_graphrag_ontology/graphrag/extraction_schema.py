"""
extraction_schema.py — Schéma commun aux 3 approches d'extraction (spaCy NER, VLM few-shot,
VLM structured output) sur les documents AFAC prétraités.

Pourquoi un schéma partagé : la comparaison spaCy vs VLM n'a de sens que si les sorties
atterrissent dans le même format. Sans ça, on compare du texte libre à du JSON et
l'évaluation devient impossible à automatiser.

Vocabulaire OUVERT, pas d'ontologie fermée ici. `ontology/afac_ontology.py` (Document,
Theme, System, Code, ...) reste la référence pour build_kg.py (construction du graphe en
production), mais elle est encore immature. Pour cette étape de comparaison exploratoire,
on ne force ni les labels d'entités ni les types de relations : le VLM choisit lui-même le
nom qui lui semble le plus juste. Voir l'usage de normalize_name ci-dessous — la seule
chose empruntée à l'ontologie est la normalisation de casse/orthographe (GEDO vs gedo),
pas la liste de labels.

Asymétrie assumée entre les 2 familles :
  - spaCy (modèle générique fr_core_news_sm) sort des labels CoNLL génériques
    (PER, LOC, ORG, MISC) et ne fait pas d'extraction de relations.
  - Les 2 approches VLM sortent des labels et des relations en vocabulaire libre,
    potentiellement différent d'un appel à l'autre.
  On ne force donc aucun mapping entre les 3 sorties. La comparaison se fait sur le texte
  de surface normalisé (l'entité a-t-elle été détectée, indépendamment du label choisi),
  pas sur l'accord de label. Voir compare_extractions.py.
"""
from __future__ import annotations

import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

THIS_DIR = Path(__file__).resolve().parent
KG_DIR = THIS_DIR.parent
for p in (str(KG_DIR),):
    if p not in sys.path:
        sys.path.insert(0, p)

from ontology.afac_ontology import normalize_name  # noqa: E402


class ExtractedEntity(BaseModel):
    """Une entité extraite, quelle que soit la méthode."""

    text: str = Field(description="Texte de surface de l'entité, tel qu'il apparaît dans le document.")
    label: str = Field(description="Type de l'entité, en vocabulaire libre — choisi par la méthode elle-même.")
    start_char: int | None = Field(default=None, description="Offset de début dans le texte source (spaCy uniquement).")
    end_char: int | None = Field(default=None, description="Offset de fin dans le texte source (spaCy uniquement).")

    @property
    def normalized_text(self) -> str:
        """Forme canonique pour la comparaison inter-méthodes (cf. ontology.normalize_name)."""
        return normalize_name(self.text)


class ExtractedRelation(BaseModel):
    """Une relation entre deux entités (VLM uniquement — spaCy n'en produit pas)."""

    source: str = Field(description="Texte de l'entité source (doit correspondre à une entité extraite).")
    target: str = Field(description="Texte de l'entité cible (doit correspondre à une entité extraite).")
    relation: str = Field(description="Type de la relation, en vocabulaire libre — choisi par le VLM lui-même.")


class ExtractionResult(BaseModel):
    """Sortie d'une méthode d'extraction pour un document donné."""

    doc_name: str
    method: str = Field(description='"spacy" | "vlm_fewshot" | "vlm_structured"')
    model_name: str | None = None
    char_count: int
    entities: list[ExtractedEntity]
    relations: list[ExtractedRelation] = Field(default_factory=list)
    extracted_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_json_file(self, path: Path) -> None:
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")


class VlmExtractionOutput(BaseModel):
    """Schéma Pydantic passé tel quel au VLM pour la sortie structurée (approche 3).

    Les champs `label` / `relation` sont des chaînes libres : on ne les contraint pas à un
    Literal, précisément parce que l'ontologie n'est pas encore stabilisée et qu'on veut
    observer le vocabulaire que le modèle choisit naturellement.
    """

    entities: list[ExtractedEntity]
    relations: list[ExtractedRelation] = Field(default_factory=list)


def dedupe_entities_and_relations(
    entities: list[ExtractedEntity], relations: list[ExtractedRelation]
) -> tuple[list[ExtractedEntity], list[ExtractedRelation]]:
    """Fusionne les entités/relations accumulées sur plusieurs chunks d'un même document
    (cf. extraction_vlm_common.chunk_text) : dédoublonne les entités par texte normalisé
    (garde la 1ère occurrence) et les relations par triplet normalisé. Limite assumée : une
    relation entre 2 entités situées dans des chunks différents n'est jamais capturée, chaque
    chunk étant extrait indépendamment — même limite que le chunking déjà utilisé par
    build_kg.py en production."""
    seen_entities: dict[str, ExtractedEntity] = {}
    for e in entities:
        key = e.normalized_text.lower()
        if key not in seen_entities:
            seen_entities[key] = e

    seen_relation_keys: set[tuple[str, str, str]] = set()
    deduped_relations: list[ExtractedRelation] = []
    for r in relations:
        key = (normalize_name(r.source).lower(), r.relation, normalize_name(r.target).lower())
        if key not in seen_relation_keys:
            seen_relation_keys.add(key)
            deduped_relations.append(r)

    return list(seen_entities.values()), deduped_relations


def label_counts(result: ExtractionResult) -> Counter[str]:
    """Distribution des labels d'entités utilisés — utile pour observer le vocabulaire que
    chaque méthode invente spontanément (matière première pour affiner l'ontologie plus tard)."""
    return Counter(e.label for e in result.entities)


def relation_type_counts(result: ExtractionResult) -> Counter[str]:
    """Distribution des types de relations utilisés (VLM uniquement)."""
    return Counter(r.relation for r in result.relations)