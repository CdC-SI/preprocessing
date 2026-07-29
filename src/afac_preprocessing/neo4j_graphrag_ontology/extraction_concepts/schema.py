"""
schema.py — Schéma commun à l'extraction de concepts AFAC (ticket Jira 548) : niveau document
(`DocConcepts`) et niveau thème (`ThemeConcepts`, produit par aggregate_theme_concepts.py).

`spacy_keywords` (signal statistique, TF-IDF corpus) et `vlm_concepts` (signal sémantique,
extraction libre Qwen thinking) restent deux listes distinctes dans `DocConcepts` — pas de
fusion floue mots-clés/concepts (cf. limite déjà assumée dans graphrag/extraction_schema.py
pour la comparaison spaCy vs VLM : deux méthodes hétérogènes, on juxtapose plutôt que de
forcer un matching incertain sur un corpus de 20 documents). Les noms de champs portent la
méthode d'origine en préfixe pour lever toute ambiguïté à la lecture du JSON.

Aucune dépendance à ontology/afac_ontology.py ici (ni normalize_name, ni NAME_ALIASES) :
cette ontologie a été construite sur ce même corpus et porte des limites déjà connues
(variantes de casse non couvertes) — s'en servir pour normaliser CETTE extraction serait
circulaire (cf. docstring de concept_extraction_llm.py pour le même principe).
`normalize_concepts()` ne fait donc qu'un nettoyage local (espaces, casse pour le
dédoublonnage), sans référentiel externe.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field


class Keyword(BaseModel):
    """Un terme extrait par TF-IDF sur l'ensemble du corpus (cf. keyword_extraction.py)."""

    term: str = Field(description="Terme (lemme), tel que retourné par le vectorizer.")
    count: int = Field(description="Nombre brut d'occurrences du terme dans CE document (fréquence intra-document).")
    score: float = Field(description="Score TF-IDF du terme pour ce document (fréquence pondérée par la rareté dans le corpus).")
    doc_freq: int = Field(description="Nombre de documents du CORPUS (sur 20) où ce terme apparaît au moins une fois — pas un compte dans ce document.")


class ConceptComparison(BaseModel):
    """Recouvrement entre `vlm_concepts` (Qwen) et `spacy_keywords` (TF-IDF, uni+bigrammes)
    pour un même document, réparti en 3 seaux exclusifs — cf. compare_concepts.py. Persisté
    ici (et pas seulement affiché en console) pour une approche data-first : ce recouvrement
    est lui-même une donnée à conserver, pas un résultat jetable recalculé à la demande.

    Le matching est like-for-like : un concept VLM multi-mots (ex. "Confirmation
    d'adhésion") n'est en `agreement` que si un BIGRAMME entier de spacy_keywords le
    corrobore, pas un mot isolé partagé par hasard (ex. "Domicile en Suisse" ne doit pas
    matcher juste parce que "suisse" est un mot-clé — cf. compare_concepts.py:_bigrams)."""

    agreement: list[str] = Field(description="Concepts VLM corroborés par la stat (mot ou bigramme partagé) — candidats haute confiance pour l'ontologie.")
    vlm_only: list[str] = Field(description="Concepts VLM sans aucun écho statistique — signal purement sémantique du VLM.")
    stat_only: list[str] = Field(description="Termes spaCy (uni/bigrammes) absents de tout concept VLM — bruit résiduel ou omission du VLM à surveiller.")


class DocConcepts(BaseModel):
    """Sortie de l'extraction de concepts pour un document."""

    doc_name: str
    theme: str
    char_count: int
    spacy_keywords: list[Keyword] = Field(default_factory=list, description="Mots-clés TF-IDF (spaCy + sklearn) — cf. keyword_extraction.py.")
    vlm_concepts_raw: list[str] = Field(default_factory=list, description="Concepts bruts renvoyés par le VLM (Qwen), avant nettoyage — cf. concept_extraction_llm.py.")
    vlm_concepts: list[str] = Field(default_factory=list, description="vlm_concepts_raw nettoyés (espaces) et dédupliqués (casse) — sans référentiel externe.")
    comparison: ConceptComparison | None = Field(default=None, description="Recouvrement vlm_concepts/spacy_keywords — cf. compare_concepts.py.")
    extracted_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_json_file(self, path: Path) -> None:
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")


class ThemeConceptRow(BaseModel):
    """Un concept agrégé au niveau thème, avec sa couverture documentaire."""

    name: str
    doc_coverage: int
    docs: list[str]


class ThemeKeywordRow(BaseModel):
    """Un terme spaCy (uni/bigramme) agrégé au niveau thème — le pendant data-first de
    ThemeConceptRow, pour que le signal statistique ne s'arrête pas au niveau document."""

    term: str
    doc_coverage: int
    total_count: int = Field(description="Somme des occurrences brutes du terme sur tous les documents du thème.")


class ThemeConcepts(BaseModel):
    """Rollup des concepts d'un thème — sortie de aggregate_theme_concepts.py, support de
    relecture manuelle pour valider/compléter les concepts généraux du thème.

    `concepts` reste le rollup VLM historique (couverture documentaire des vlm_concepts).
    `keyword_rollup` fait remonter le signal spaCy au niveau thème (absent avant cette
    passe). `theme_agreement/vlm_only/stat_only` agrègent les 3 seaux de ConceptComparison
    de chaque document — la vue accord/désaccord au niveau catégorie, pas seulement doc."""

    theme: str
    doc_count: int
    source_documents: list[str] = Field(default_factory=list, description="Documents du corpus ayant contribué à ce rollup, pour monitoring/traçabilité.")
    concepts: list[ThemeConceptRow] = Field(default_factory=list)
    keyword_rollup: list[ThemeKeywordRow] = Field(default_factory=list, description="Mots-clés spaCy agrégés sur le thème — pendant data-first de `concepts`.")
    theme_agreement: list[ThemeConceptRow] = Field(default_factory=list, description="Concepts en accord VLM/stat dans au moins un doc — candidats haute confiance pour l'ontologie du thème.")
    theme_vlm_only: list[str] = Field(default_factory=list, description="Concepts jamais corroborés par la stat sur tout le thème.")
    theme_stat_only: list[str] = Field(default_factory=list, description="Termes stat fréquents sur le thème mais absents de tout concept VLM.")
    generated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_json_file(self, path: Path) -> None:
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")


def normalize_concepts(raw_concepts: list[str]) -> list[str]:
    """Nettoie (espaces superflus) et déduplique (casse) une liste de concepts bruts, en
    conservant l'ordre de première apparition. Pas de référentiel externe (cf. docstring du
    module) : c'est un nettoyage local, pas une canonicalisation métier."""
    seen: dict[str, str] = {}
    for raw in raw_concepts:
        cleaned = " ".join(raw.strip().split())
        key = cleaned.lower()
        if key not in seen:
            seen[key] = cleaned
    return list(seen.values())
