"""
build_kg_from_concepts.py — Construit le graphe Neo4j "concept-guided" pour TOUS les documents
d'un thème, à partir des concepts déjà extraits (extraction_concepts/output/*.json).

Objectif (cf. discussion) : se rapprocher du comportement de batch_build_kg.py — un même
concept rencontré dans plusieurs documents (ex. GEDO) doit fusionner en un seul nœud, reliant
les documents entre eux — tout en laissant les PRÉDICATS totalement libres au VLM (pas de
RELATIONSHIP_TYPES fermé comme dans ontology/afac_ontology.py).

Supersede load_manual_triples_vlm.py pour l'usage batch (ce dernier reste tel quel : test
exploratoire single-doc, comparant lecture manuelle et VLM sur Mineur uniquement).

Design en 5 points :
  1. ThemeVocabulary   — vocabulaire d'entités (nom canonique + type) fixé UNE SEULE FOIS pour
     tout le thème, à partir de `_theme_<Thème>.json` (ThemeConcepts.concepts, déjà agrégé par
     aggregate_theme_concepts.py) — pas re-décidé par document (sinon GEDO pourrait être typé
     différemment selon le doc, ce qui casserait la fusion inter-documents).
  2. EntityNameNormalizer — normalise le nom AVANT le chargement (réutilise
     ontology.afac_ontology.normalize_name : un simple nettoyage de chaîne — alias connus,
     casse, espaces — PAS l'ontologie fermée elle-même, donc pas circulaire ici).
  3. ConceptGraphLoader — MERGE sur un label d'ancrage STABLE (`ConceptTest`) jamais retiré
     (contrairement à load_manual_triples_vlm.py qui faisait `REMOVE e:Entity` après typage,
     ce qui cassait le merge du document suivant). Le type métier (point 1) est ajouté EN PLUS
     de cet ancrage, jamais à sa place.
  4. ConceptRelationExtractor — un appel VLM par chunk, ancré sur le vocabulaire fermé du
     thème, mais SANS contrainte sur le vocabulaire des relations : prédicats 100% libres.
  5. Après le batch : réutilise `GraphNormalizer` (shared/kg_shared_utils.py, module partagé
     avec `graphrag/batch_build_kg.py`) pour un rattrapage des variantes de casse/orthographe
     résiduelles — sans risque de fusion croisée avec le graphe historique de
     batch_build_kg.py : nos nœuds portent le label d'ancrage
     `ConceptTest` (absent du graphe historique) + des labels de type toujours en MAJUSCULES
     ASCII (sanitize_identifier), qui ne collisionnent jamais avec les labels mixtes-case de
     ontology/afac_ontology.py (ex. "System" vs "SYSTEME").

  6. Provenance/citations — LexicalGraphLoader crée, pour chaque document, un vrai graphe
     lexical (`TextSource`/`TextChunk`, convention neo4j-graphrag vérifiée : `(chunk)-[:
     FROM_DOCUMENT]->(document)`, `(chunk)-[:NEXT_CHUNK]->(chunk_suivant)`) — contrairement au
     pipeline historique (`run_async(text=...)`), le `TextSource` est identifié par le vrai
     `doc_name` et porte le chemin du fichier source réel (`source_path`, via
     SourceFileResolver — n'importe quel format, pas seulement PDF). Chaque entité est reliée
     au chunk PRÉCIS où elle a été trouvée (`(entité)-[:FROM_CHUNK]->(chunk)`), pas juste "quelque
     part dans le document" — permet de citer la source exacte d'une réponse GraphRAG (cf.
     test_graphrag_question.py) et sert de base à un futur index BM25/vectoriel sur
     `TextChunk.text`.

Isolation du graphe existant : tout est tagué `test_source: "concept_kg_<thème>"` (propriété,
pas un label), comme load_manual_triples_vlm.py — aucune donnée du graphe historique n'est lue
ni modifiée.

Usage :
    cd preprocessing/src/afac-preprocessing
    uv run --active python neo4j_graphrag_ontology/extraction_concepts/build_kg_from_concepts.py \
        --theme Adhésion --dotenv .env.test

    # Ne traiter que certains documents (répétable) :
    uv run --active python neo4j_graphrag_ontology/extraction_concepts/build_kg_from_concepts.py \
        --theme Adhésion --doc-name Mineur --doc-name Globe-trotter

    # Forcer le recalcul du glossaire de types (sinon mis en cache sur disque) :
    uv run --active python neo4j_graphrag_ontology/extraction_concepts/build_kg_from_concepts.py \
        --theme Adhésion --retype

    # Nettoyer avant de relancer :
    uv run --active python neo4j_graphrag_ontology/extraction_concepts/build_kg_from_concepts.py \
        --theme Adhésion --wipe
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import neo4j
from dotenv import load_dotenv

THIS_DIR = Path(__file__).resolve().parent            # .../extraction_concepts
KG_DIR = THIS_DIR.parent                                # .../neo4j_graphrag_ontology
PROJECT_ROOT = KG_DIR.parent                            # .../afac-preprocessing
for p in (str(PROJECT_ROOT), str(KG_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from neo4j_graphrag.embeddings import OpenAIEmbeddings  # noqa: E402
from neo4j_graphrag.indexes import create_vector_index  # noqa: E402

from ontology.afac_ontology import normalize_name  # noqa: E402
from shared.extraction_vlm_common import (  # noqa: E402
    DEFAULT_OUTPUT_DIR,
    DOMAIN_CONTEXT,
    DocumentLocator,
    TextChunker,
    TolerantJsonParser,
)
from shared.kg_shared_utils import EmbedderFactory, GraphNormalizer  # noqa: E402
from schema import DocConcepts, ThemeConcepts  # noqa: E402
from utils.vlm_client import build_sync_client, build_vlm_config, text_completion  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
_log = logging.getLogger("build_kg_from_concepts")

CONCEPTS_DIR = THIS_DIR / "output"  # sortie de extract_doc_concepts.py / aggregate_theme_concepts.py
INPUT_FILES_DIR = PROJECT_ROOT / "data" / "input_files" / "afac"  # racine des documents source
ANCHOR_LABEL = "ConceptTest"  # label stable jamais retiré — distinct du label métier "Concept"
                              # du graphe historique (ontology/afac_ontology.py), pour ne jamais
                              # interférer avec lui (cf. point 5 de la docstring de module).
CHUNK_VECTOR_INDEX_NAME = "concept_kg_chunk_embeddings"  # un seul index, partagé entre thèmes
                              # (même label TextChunk, même modèle d'embedding) — filtré par
                              # test_source/doc_name au moment de la recherche, pas par thème.
EMBEDDING_DIMENSION = 1024  # vérifié sur un embedding.json existant du pipeline (même modèle
                              # EMBEDDING_MODEL_NAME que embedding_metadata.py)


class SourceFileResolver:
    """Retrouve le fichier source d'un document, quel que soit son format (PDF aujourd'hui,
    mais potentiellement .docx/.txt/... demain pour d'autres thèmes) — recherche par nom sans
    supposer d'extension, plutôt qu'un chemin `<doc_name>.pdf` codé en dur."""

    def __init__(self, input_files_dir: Path = INPUT_FILES_DIR) -> None:
        self.input_files_dir = input_files_dir

    def resolve(self, theme: str, doc_name: str) -> Path | None:
        theme_dir = self.input_files_dir / theme
        if not theme_dir.is_dir():
            _log.warning("Dossier source introuvable pour le thème « %s » (%s).", theme, theme_dir)
            return None
        # Comparaison sur les espaces normalisés (pas un glob exact) : certains fichiers du
        # corpus ont un espace parasite avant l'extension (ex. "Lacunes d'assurance .pdf").
        target = " ".join(doc_name.split())
        matches = sorted(
            p for p in theme_dir.iterdir()
            if p.is_file() and " ".join(p.stem.split()) == target
        )
        if not matches:
            _log.warning("Fichier source introuvable pour « %s » dans %s.", doc_name, theme_dir)
            return None
        if len(matches) > 1:
            _log.warning("Plusieurs fichiers source pour « %s » (%s) — le premier est retenu.",
                         doc_name, ", ".join(m.name for m in matches))
        return matches[0]


def chunk_uid(doc_name: str, index: int) -> str:
    """Identifiant stable d'un chunk (doc + position) — utilisé pour le MERGE des `TextChunk`
    (LexicalGraphLoader) et pour les relier aux entités qui en sont issues (ConceptGraphLoader),
    sans dépendre d'un ID interne Neo4j."""
    return f"{doc_name}::{index}"


def sanitize_identifier(text: str) -> str:
    """Texte libre -> identifiant Cypher sûr (SNAKE_CASE, [A-Z0-9_]+ uniquement). Réutilisé pour
    les types de relation (prédicats VLM) et les labels de nœud (types d'entités VLM) : même
    besoin, même charset restreint, donc pas d'injection possible en interpolant le résultat
    dans une requête Cypher (les noms de type/label ne sont pas paramétrables nativement).
    Rapatrié depuis load_manual_triples_vlm.py (archivé dans trash/) — seul consommateur
    aujourd'hui."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    snake = re.sub(r"[^a-zA-Z0-9]+", "_", ascii_text).strip("_").upper()
    return snake or "REL"


class EntityNameNormalizer:
    """Enveloppe POO autour de ontology.afac_ontology.normalize_name() — un simple nettoyage de
    chaîne (alias connus + casse/espaces), PAS l'ontologie fermée (labels/relations) elle-même :
    la réutiliser ici n'est donc pas circulaire (cf. schema.py). Isolée dans sa propre classe
    pour rester interchangeable (une autre stratégie de canonicalisation pourrait la remplacer
    sans toucher au reste du pipeline)."""

    def normalize(self, name: str) -> str:
        return normalize_name(name)


class ConceptTyper:
    """Type chaque concept d'un thème UNE SEULE FOIS (pas par document) — évite qu'un même
    concept (ex. GEDO) reçoive un type différent selon le document où on le rencontre, ce qui
    casserait la fusion inter-documents. Découpé en lots (`batch_size`) : un thème comme
    Adhésion compte ~400 concepts bruts, trop pour un seul appel JSON sans risque de
    troncature — mais chaque concept reste typé une seule fois au niveau thème, jamais
    re-décidé par document."""

    def __init__(self, client, model: str, *, batch_size: int = 60) -> None:
        self.client = client
        self.model = model
        self.batch_size = batch_size
        self._json_parser = TolerantJsonParser()

    @staticmethod
    def _build_prompt(concepts: list[str]) -> str:
        concepts_block = "\n".join(f"- {c}" for c in concepts)
        return f"""{DOMAIN_CONTEXT}

Voici la liste FERMÉE des concepts métier déjà identifiés pour ce thème (tous documents \
confondus) — n'en invente aucun autre, ne les reformule pas :
{concepts_block}

Donne à CHAQUE concept un type court et cohérent (ex. Système, Code, Condition, Document, \
Acteur, Processus, Référence légale...). Deux concepts de même nature doivent recevoir \
EXACTEMENT le même type (même orthographe, même casse) — c'est essentiel pour que les concepts \
partagés entre documents restent reconnus comme identiques. Choisis toi-même le vocabulaire de \
types, il n'y a pas de liste fermée à respecter.

Retourne un objet JSON de la forme {{"types": {{"<concept>": "<type>", ...}}}} avec une entrée \
pour CHAQUE concept de la liste ci-dessus. N'entoure pas le JSON de backticks. Ne renvoie que \
le JSON, rien d'autre.
"""

    def assign_types(self, concepts: list[str]) -> dict[str, str]:
        if not concepts:
            return {}
        glossary: dict[str, str] = {}
        batches = [concepts[i:i + self.batch_size] for i in range(0, len(concepts), self.batch_size)]
        for i, batch in enumerate(batches, 1):
            _log.info("  typage lot %d/%d (%d concepts)…", i, len(batches), len(batch))
            system_prompt = self._build_prompt(batch)
            raw = text_completion(self.client, self.model, system_prompt, "Assigne les types demandés.")
            parsed = self._json_parser.parse(raw)
            types = parsed.get("types", {})
            glossary.update({c: types.get(c, "Concept") for c in batch})
        return glossary


class ThemeVocabulary:
    """Vocabulaire fermé d'un thème : nom canonique -> type, décidé UNE SEULE FOIS (pas par
    document). Construit à partir du rollup `_theme_<Thème>.json` (ThemeConcepts.concepts, déjà
    agrégé par aggregate_theme_concepts.py). Le typage (ConceptTyper, un seul appel VLM) est mis
    en cache sur disque pour ne pas re-payer un appel VLM à chaque run."""

    def __init__(self, theme: str, concepts_dir: Path, normalizer: EntityNameNormalizer) -> None:
        self.theme = theme
        self.concepts_dir = concepts_dir
        self.normalizer = normalizer
        self._glossary: dict[str, str] | None = None

    def _safe_theme(self) -> str:
        return self.theme.replace("/", "_")

    @property
    def theme_json_path(self) -> Path:
        return self.concepts_dir / f"_theme_{self._safe_theme()}.json"

    @property
    def glossary_cache_path(self) -> Path:
        return self.concepts_dir / f"_theme_{self._safe_theme()}_glossary.json"

    def _load_theme_concepts(self) -> ThemeConcepts:
        if not self.theme_json_path.exists():
            raise FileNotFoundError(
                f"Rollup thème introuvable ({self.theme_json_path}). Lancer d'abord : "
                "uv run --active python neo4j_graphrag_ontology/extraction_concepts/aggregate_theme_concepts.py"
            )
        return ThemeConcepts.model_validate_json(self.theme_json_path.read_text(encoding="utf-8"))

    def source_documents(self) -> list[str]:
        return self._load_theme_concepts().source_documents

    def concept_names(self) -> list[str]:
        """Noms canoniques (dédupliqués via EntityNameNormalizer) de tous les concepts du
        thème — c'est ici que les variantes de casse (ex. "adhésion" / "Adhésion", connu comme
        limite de l'agrégation brute) se rejoignent, AVANT tout chargement dans Neo4j."""
        theme_concepts = self._load_theme_concepts()
        seen: dict[str, str] = {}
        for row in theme_concepts.concepts:
            canonical = self.normalizer.normalize(row.name)
            seen.setdefault(canonical.lower(), canonical)
        return list(seen.values())

    def build_glossary(self, typer: ConceptTyper, *, force: bool = False) -> dict[str, str]:
        if not force and self.glossary_cache_path.exists():
            self._glossary = json.loads(self.glossary_cache_path.read_text(encoding="utf-8"))
            _log.info("Glossaire de types chargé depuis le cache (%s, %d concepts).",
                       self.glossary_cache_path.name, len(self._glossary))
            return self._glossary

        names = self.concept_names()
        _log.info("Typage de %d concept(s) du thème « %s » (1 appel VLM)…", len(names), self.theme)
        self._glossary = typer.assign_types(names)
        self.glossary_cache_path.write_text(
            json.dumps(self._glossary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _log.info("Glossaire écrit : %s", self.glossary_cache_path)
        return self._glossary

    def type_of(self, name: str) -> str:
        if self._glossary is None:
            raise RuntimeError("Glossaire non construit — appeler build_glossary() d'abord.")
        canonical = self.normalizer.normalize(name)
        return self._glossary.get(canonical, "Concept")

    def concepts_for_doc(self, doc_concepts: DocConcepts) -> list[str]:
        """Sous-ensemble (canonicalisé) du vocabulaire du thème présent dans CE document —
        c'est cette liste, déjà réduite à sa forme canonique, qui est donnée au VLM comme
        vocabulaire fermé pour l'extraction des relations (ConceptRelationExtractor)."""
        by_lower = {n.lower(): n for n in self.concept_names()}
        result: list[str] = []
        seen: set[str] = set()
        for raw in doc_concepts.vlm_concepts:
            canonical = self.normalizer.normalize(raw)
            match = by_lower.get(canonical.lower())
            if match and match not in seen:
                result.append(match)
                seen.add(match)
        return result


@dataclass
class ChunkExtraction:
    """Un chunk du document + les triplets qui en ont été extraits — garde la trace de quel
    passage du texte justifie chaque relation (citation), et conserve le texte même quand
    aucune relation n'en a été extraite (utile pour un index BM25/vectoriel plus tard, qui n'a
    besoin que du texte, pas des triplets)."""

    index: int
    text: str
    triples: list[tuple[str, str, str]] = field(default_factory=list)


class ConceptRelationExtractor:
    """Un appel VLM par chunk : trouve les relations exprimées entre les concepts d'un
    vocabulaire FERMÉ (déjà typé au niveau thème par ThemeVocabulary) — les prédicats restent
    100% libres, en texte français, choisis par le VLM (aucune liste imposée, contrairement à
    ontology/afac_ontology.RELATIONSHIP_TYPES)."""

    def __init__(self, client, model: str, *, temperature: float = 0.6) -> None:
        self.client = client
        self.model = model
        self.temperature = temperature
        self._chunker = TextChunker()
        self._json_parser = TolerantJsonParser()

    @staticmethod
    def _build_prompt(concepts: list[str]) -> str:
        concepts_block = "\n".join(f"- {c}" for c in concepts)
        return f"""Tu analyses un document dont les concepts métier ont déjà été identifiés et typés. {DOMAIN_CONTEXT}

Voici la liste FERMÉE des concepts déjà repérés dans ce document — n'en invente aucun autre, \
ne les reformule pas :
{concepts_block}

Identifie dans le texte les relations exprimées ENTRE ces concepts (uniquement entre concepts \
de la liste ci-dessus). Choisis toi-même le type de chaque relation, en français, en texte \
libre — aucune liste de prédicats n'est imposée.

Retourne un objet JSON de la forme :
{{"relations": [{{"source": "...", "relation": "...", "target": "..."}}]}}

`source` et `target` doivent reprendre EXACTEMENT le texte d'un concept de la liste fournie \
(aucune paraphrase, aucun concept hors liste). N'entoure pas le JSON de backticks. Ne renvoie \
que le JSON, rien d'autre.
"""

    def extract(self, text: str, concepts: list[str]) -> list[ChunkExtraction]:
        system_prompt = self._build_prompt(concepts)
        concept_set = set(concepts)
        ignored = 0
        raw_chunks = self._chunker.split(text)
        results: list[ChunkExtraction] = []

        for i, chunk in enumerate(raw_chunks):
            extraction = ChunkExtraction(index=i, text=chunk)
            results.append(extraction)

            raw = text_completion(self.client, self.model, system_prompt, chunk, temperature=self.temperature)
            try:
                parsed = self._json_parser.parse(raw)
            except json.JSONDecodeError as exc:
                # JSON mal formé sur CE chunk (guillemet non échappé, réponse tronquée...) —
                # fréquent en free-form sans sortie structurée (cf. extraction_vlm_common.py).
                # Le chunk est conservé (texte brut, sans triplet) plutôt que perdu : il reste
                # exploitable pour un retrieval BM25/vectoriel même sans relation extraite.
                # exc.pos pointe l'octet exact où json.loads a échoué — on affiche le contexte
                # autour (pas juste les 200 premiers car., qui coupent souvent avant le problème).
                start, end = max(0, exc.pos - 120), min(len(raw), exc.pos + 40)
                _log.warning(
                    "Chunk %d/%d : JSON invalide (%s) — longueur réponse=%d, contexte autour de "
                    "l'erreur : …%r…",
                    i + 1, len(raw_chunks), exc, len(raw), raw[start:end],
                )
                continue

            for r in parsed.get("relations", []):
                source, target, relation = r.get("source"), r.get("target"), r.get("relation")
                if source in concept_set and target in concept_set and relation:
                    extraction.triples.append((source, relation, target))
                else:
                    ignored += 1

        if ignored:
            _log.warning("%d relation(s) ignorée(s) (source/target hors liste de concepts fournie).", ignored)
        return results


class LexicalGraphLoader:
    """Crée le graphe lexical (document + chunks) du côté "concept-guided", en reproduisant la
    convention réelle de neo4j-graphrag (vérifiée dans
    neo4j_graphrag.experimental.components.lexical_graph et .types) :
      - labels `TextSource` (document) / `TextChunk` (chunk), propriétés `text`/`index` sur le
        chunk — mêmes noms que graphrag/build_kg.py, pour rester comparable.
      - `(chunk)-[:FROM_DOCUMENT]->(document)` et `(chunk)-[:NEXT_CHUNK]->(chunk_suivant)`.

    Contrairement au pipeline historique (`run_async(text=...)`, qui pose un `path` générique
    identique pour tous les documents faute de métadonnée par appel), le nœud `TextSource` ici
    est identifié par le vrai `doc_name` ET porte le chemin du fichier source réel (`source_path`,
    résolu par SourceFileResolver — PDF aujourd'hui, autre format possible demain), directement
    interrogeable en Cypher sans reconstruire le chemin côté application.

    Isolé via `test_source` (comme ConceptGraphLoader) : ne touche jamais un éventuel graphe
    lexical historique reconstruit plus tard par batch_build_kg.py.

    `embedder` (optionnel) : si fourni, chaque chunk est embedé à la création et stocké sur
    `c.embedding` — base du retriever vectoriel officiel (neo4j_graphrag.retrievers.
    VectorRetriever, cf. test_graphrag_question.py) plutôt qu'une recherche par mots-clés."""

    def __init__(
        self, driver: neo4j.Driver, source: str, resolver: SourceFileResolver,
        embedder: OpenAIEmbeddings | None = None,
    ) -> None:
        self.driver = driver
        self.source = source
        self.resolver = resolver
        self.embedder = embedder

    def create_document(self, doc_name: str, theme: str) -> None:
        source_path = self.resolver.resolve(theme, doc_name)
        self.driver.execute_query(
            """
            MERGE (d:TextSource {name: $doc_name, test_source: $source})
            SET d.theme = $theme,
                d.source_path = $source_path,
                d.source_extension = $source_extension
            """,
            doc_name=doc_name, source=self.source, theme=theme,
            source_path=str(source_path) if source_path else None,
            source_extension=source_path.suffix if source_path else None,
        )

    def create_chunks(self, doc_name: str, chunks: list["ChunkExtraction"]) -> None:
        for chunk in chunks:
            uid = chunk_uid(doc_name, chunk.index)
            embedding = self.embedder.embed_query(chunk.text) if self.embedder else None
            self.driver.execute_query(
                """
                MERGE (c:TextChunk {chunk_uid: $uid, test_source: $source})
                SET c.text = $text, c.index = $index, c.doc_name = $doc_name
                FOREACH (_ IN CASE WHEN $embedding IS NOT NULL THEN [1] ELSE [] END |
                    SET c.embedding = $embedding
                )
                WITH c
                MATCH (d:TextSource {name: $doc_name, test_source: $source})
                MERGE (c)-[:FROM_DOCUMENT]->(d)
                """,
                uid=uid, source=self.source, text=chunk.text, index=chunk.index, doc_name=doc_name,
                embedding=embedding,
            )
            if chunk.index > 0:
                # Sens réel de neo4j-graphrag (lexical_graph.py) : chunk précédent -> chunk
                # suivant (chronologique), pas l'inverse.
                self.driver.execute_query(
                    """
                    MATCH (prev:TextChunk {chunk_uid: $prev_uid, test_source: $source})
                    MATCH (curr:TextChunk {chunk_uid: $uid, test_source: $source})
                    MERGE (prev)-[:NEXT_CHUNK]->(curr)
                    """,
                    prev_uid=chunk_uid(doc_name, chunk.index - 1), uid=uid, source=self.source,
                )

    def ensure_vector_index(self) -> None:
        """Crée l'index vectoriel officiel (neo4j_graphrag.indexes, pas de Cypher fait main) sur
        `TextChunk.embedding` — idempotent (`fail_if_exists=False` : no-op si déjà créé), à
        appeler une fois après le batch.

        Pas de `filterable_properties` : cette instance Neo4j tourne en édition Community
        (vérifié via `dbms.components()`), qui ne supporte pas le filtrage de propriétés DANS
        l'index vectoriel (clause `WITH [...]`, réservée à l'édition Enterprise — la requête
        `CREATE VECTOR INDEX` échoue sinon avec `CypherSyntaxError`). Le filtre `test_source`
        passé à `VectorRetriever.search(filters=...)` (cf. test_graphrag_question.py) continue
        de fonctionner : la librairie bascule automatiquement sur son fallback "procédure +
        filtrage Cypher en aval", juste moins optimisé — sans impact vu la taille du graphe."""
        if not self.embedder:
            return
        create_vector_index(
            self.driver,
            name=CHUNK_VECTOR_INDEX_NAME,
            label="TextChunk",
            embedding_property="embedding",
            dimensions=EMBEDDING_DIMENSION,
            similarity_fn="cosine",
            fail_if_exists=False,
        )
        _log.info("Index vectoriel « %s » prêt (label TextChunk, %d dimensions).",
                   CHUNK_VECTOR_INDEX_NAME, EMBEDDING_DIMENSION)


class ConceptGraphLoader:
    """Charge triplets + types dans Neo4j avec une identité de fusion STABLE : le label
    d'ancrage `ConceptTest` n'est jamais retiré (contrairement à load_manual_triples_vlm.py, où
    `REMOVE e:Entity` après typage cassait le merge du document suivant) — un même concept
    (ex. GEDO) rencontré dans plusieurs documents fusionne donc automatiquement sur tout le
    batch, peu importe l'ordre de traitement des documents. Le type métier (ThemeVocabulary)
    est ajouté EN PLUS de cet ancrage, jamais à sa place."""

    def __init__(self, driver: neo4j.Driver, vocabulary: ThemeVocabulary, source: str) -> None:
        self.driver = driver
        self.vocabulary = vocabulary
        self.source = source

    def load_triples(self, chunks: list[ChunkExtraction], doc_name: str) -> None:
        """Charge les triplets de chaque chunk et relie précisément sujet/objet au chunk où ce
        triplet a été trouvé (`(entité)-[:FROM_CHUNK]->(chunk)`, même convention que le pipeline
        historique) — provenance fine (le chunk exact), pas juste "quelque part dans ce doc"."""
        for chunk in chunks:
            uid = chunk_uid(doc_name, chunk.index)
            for subject, predicate, obj in chunk.triples:
                rel_type = sanitize_identifier(predicate)
                subject_type_fr = self.vocabulary.type_of(subject)
                object_type_fr = self.vocabulary.type_of(obj)
                subject_label = sanitize_identifier(subject_type_fr)
                object_label = sanitize_identifier(object_type_fr)
                # rel_type/subject_label/object_label sont assainis en amont ([A-Z0-9_]+) : pas
                # d'injection possible en les interpolant dans la requête Cypher (les noms de
                # label/type de relation ne sont pas paramétrables nativement).
                query = f"""
                MERGE (s:{ANCHOR_LABEL} {{name: $subject}})
                  ON CREATE SET s.test_source = $source
                SET s:{subject_label}, s.entity_type_fr = $subject_type_fr
                MERGE (o:{ANCHOR_LABEL} {{name: $object}})
                  ON CREATE SET o.test_source = $source
                SET o:{object_label}, o.entity_type_fr = $object_type_fr
                MERGE (s)-[r:{rel_type}]->(o)
                SET r.predicate_fr = $predicate, r.test_source = $source, r.doc_name = $doc_name
                WITH s, o
                MATCH (c:TextChunk {{chunk_uid: $chunk_uid, test_source: $source}})
                MERGE (s)-[:FROM_CHUNK]->(c)
                MERGE (o)-[:FROM_CHUNK]->(c)
                """
                self.driver.execute_query(
                    query,
                    subject=subject, object=obj, predicate=predicate,
                    subject_type_fr=subject_type_fr, object_type_fr=object_type_fr,
                    source=self.source, doc_name=doc_name, chunk_uid=uid,
                )

    def wipe(self) -> None:
        self.driver.execute_query("MATCH (n {test_source: $source}) DETACH DELETE n", source=self.source)

    def ensure_entity_source_index(self) -> None:
        """Index simple (range, pas vectoriel) sur {ANCHOR_LABEL}.test_source — label fixe
        partagé par toutes les entités (contrairement aux labels métier / types de relation,
        dynamiques et donc impossibles à indexer un par un). Sert VectorGraphContextRetriever
        (test_graphrag_question.py), qui filtre chaque nœud traversé par test_source à chaque
        appel — sans cet index, chaque recherche scanne tout le label. IF NOT EXISTS : idempotent."""
        self.driver.execute_query(
            f"CREATE INDEX concept_test_source_idx IF NOT EXISTS FOR (n:{ANCHOR_LABEL}) ON (n.test_source)"
        )

    def summary(self) -> None:
        _LEXICAL_TYPES = ["FROM_CHUNK", "FROM_DOCUMENT", "NEXT_CHUNK"]
        nodes, _, _ = self.driver.execute_query(
            f"MATCH (n:{ANCHOR_LABEL} {{test_source: $source}}) RETURN count(n) AS c", source=self.source
        )
        chunks, _, _ = self.driver.execute_query(
            "MATCH (c:TextChunk {test_source: $source}) RETURN count(c) AS c", source=self.source
        )
        docs, _, _ = self.driver.execute_query(
            "MATCH (d:TextSource {test_source: $source}) RETURN count(d) AS c", source=self.source
        )
        rels, _, _ = self.driver.execute_query(
            "MATCH ()-[r {test_source: $source}]->() WHERE NOT type(r) IN $lexical RETURN count(r) AS c",
            source=self.source, lexical=_LEXICAL_TYPES,
        )
        by_type, _, _ = self.driver.execute_query(
            f"""
            MATCH (n:{ANCHOR_LABEL} {{test_source: $source}})
            RETURN coalesce(n.entity_type_fr, '(non typé)') AS type, count(*) AS c
            ORDER BY c DESC
            """,
            source=self.source,
        )
        print(f"\nDocuments (TextSource) : {docs[0]['c']}")
        print(f"Chunks (TextChunk) : {chunks[0]['c']}")
        print(f"Nœuds concepts ({ANCHOR_LABEL}) : {nodes[0]['c']}")
        print(f"Relations métier (hors FROM_CHUNK/FROM_DOCUMENT/NEXT_CHUNK) : {rels[0]['c']}")
        print("Répartition par type :")
        for r in by_type:
            print(f"  {r['type']}: {r['c']}")


class ConceptKGBatchBuilder:
    """Orchestre le pipeline complet sur tous les documents d'un thème : vocabulaire fermé
    (ThemeVocabulary) -> extraction des relations par document (ConceptRelationExtractor) ->
    chargement Neo4j (ConceptGraphLoader) -> passe de rattrapage finale (normalize_pass,
    réutilisée telle quelle depuis graphrag/batch_build_kg.py)."""

    def __init__(
        self,
        vocabulary: ThemeVocabulary,
        extractor: ConceptRelationExtractor,
        loader: ConceptGraphLoader,
        lexical_loader: LexicalGraphLoader,
        theme: str,
        concepts_dir: Path,
        output_dir: Path,
    ) -> None:
        self.vocabulary = vocabulary
        self.extractor = extractor
        self.loader = loader
        self.lexical_loader = lexical_loader
        self.theme = theme
        self.concepts_dir = concepts_dir
        self.output_dir = output_dir
        self._doc_locator = DocumentLocator(output_dir)

    def _load_doc_concepts(self, doc_name: str) -> DocConcepts:
        path = self.concepts_dir / f"{doc_name}.json"
        if not path.exists():
            raise FileNotFoundError(
                f"Aucun concept extrait pour « {doc_name} » ({path}). Lancer d'abord "
                "extract_doc_concepts.py / batch_extract_concepts.py."
            )
        return DocConcepts.model_validate_json(path.read_text(encoding="utf-8"))

    def run_document(self, doc_name: str) -> None:
        doc_concepts = self._load_doc_concepts(doc_name)
        concepts = self.vocabulary.concepts_for_doc(doc_concepts)
        if not concepts:
            _log.warning("%s : aucun concept du vocabulaire thème trouvé — document ignoré.", doc_name)
            return

        text = self._doc_locator.resolve_final_md(doc_name).read_text(encoding="utf-8")

        self.lexical_loader.create_document(doc_name, self.theme)
        chunks = self.extractor.extract(text, concepts)
        self.lexical_loader.create_chunks(doc_name, chunks)
        self.loader.load_triples(chunks, doc_name)

        n_triples = sum(len(c.triples) for c in chunks)
        _log.info("%s : %d concept(s) ancré(s), %d chunk(s), %d relation(s) chargée(s).",
                   doc_name, len(concepts), len(chunks), n_triples)

    def run_batch(self, doc_names: list[str], driver: neo4j.Driver) -> None:
        ok, failed = 0, []
        for i, doc in enumerate(doc_names, 1):
            try:
                _log.info("[%d/%d] %s", i, len(doc_names), doc)
                self.run_document(doc)
                ok += 1
            except Exception:
                _log.exception("[%d/%d] ÉCHEC %s — batch poursuivi.", i, len(doc_names), doc)
                failed.append(doc)

        _log.info("Chargement terminé : %d ok, %d échecs.", ok, len(failed))
        if failed:
            _log.warning("Documents en échec : %s", ", ".join(failed))

        _log.info("Passe de rattrapage finale (GraphNormalizer, réutilisé depuis shared/kg_shared_utils.py)…")
        GraphNormalizer(driver).run()

        self.lexical_loader.ensure_vector_index()
        self.loader.ensure_entity_source_index()
        self.loader.summary()


def build_driver(dotenv: str) -> neo4j.Driver:
    load_dotenv(dotenv)
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "")
    driver = neo4j.GraphDatabase.driver(uri, auth=(user, password))
    driver.verify_connectivity()
    return driver


def source_tag(theme: str) -> str:
    return f"concept_kg_{normalize_name(theme).lower().replace(' ', '_')}"


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Construit le KG AFAC 'concept-guided' pour tous les documents d'un thème."
    )
    ap.add_argument("--theme", default="Adhésion")
    ap.add_argument("--dotenv", default=".env.test")
    ap.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    ap.add_argument("--concepts-dir", default=str(CONCEPTS_DIR))
    ap.add_argument("--input-files-dir", default=str(INPUT_FILES_DIR),
                     help="Racine des documents source (PDF/txt/docx/...), organisée en <thème>/<doc_name>.<ext>.")
    ap.add_argument("--doc-name", action="append", dest="doc_names",
                     help="Limiter le batch à ce document (répétable). Défaut : tous les docs du thème.")
    ap.add_argument("--retype", action="store_true", help="Force le recalcul du glossaire de types (ignore le cache).")
    ap.add_argument("--wipe", action="store_true", help="Supprime les nœuds/relations de ce test, puis quitte.")
    ap.add_argument("--no-embeddings", dest="embeddings", action="store_false",
                     help="Ne pas embedder les chunks (pas d'index vectoriel, retrieval par relations uniquement).")
    ap.add_argument("--index-only", action="store_true",
                     help="Ne (re)crée que l'index vectoriel sur les chunks déjà en base, sans relancer l'extraction.")
    args = ap.parse_args()

    driver = build_driver(args.dotenv)
    source = source_tag(args.theme)

    if args.wipe:
        driver.execute_query("MATCH (n {test_source: $source}) DETACH DELETE n", source=source)
        print(f"Nœuds/relations supprimés (test_source={source!r}).")
        driver.close()
        return

    if args.index_only:
        cfg = build_vlm_config(Path(args.dotenv))
        embedder = EmbedderFactory(cfg).build()
        resolver = SourceFileResolver(Path(args.input_files_dir))
        LexicalGraphLoader(driver, source, resolver, embedder).ensure_vector_index()
        driver.close()
        return

    normalizer = EntityNameNormalizer()
    vocabulary = ThemeVocabulary(args.theme, Path(args.concepts_dir), normalizer)

    cfg = build_vlm_config(Path(args.dotenv))
    client = build_sync_client(cfg)

    typer = ConceptTyper(client, cfg.vlm_model_name)
    vocabulary.build_glossary(typer, force=args.retype)

    extractor = ConceptRelationExtractor(client, cfg.vlm_model_name)
    loader = ConceptGraphLoader(driver, vocabulary, source)
    resolver = SourceFileResolver(Path(args.input_files_dir))
    embedder = EmbedderFactory(cfg).build() if args.embeddings else None
    if not args.embeddings:
        _log.info("Embeddings désactivés (--no-embeddings) — pas d'index vectoriel créé.")
    lexical_loader = LexicalGraphLoader(driver, source, resolver, embedder)
    builder = ConceptKGBatchBuilder(
        vocabulary, extractor, loader, lexical_loader, args.theme, Path(args.concepts_dir), Path(args.output_dir)
    )

    doc_names = args.doc_names or vocabulary.source_documents()
    _log.info("%d document(s) à charger pour le thème « %s » : %s", len(doc_names), args.theme, ", ".join(doc_names))

    builder.run_batch(doc_names, driver)
    driver.close()
    print(f"\nGraphe concept-guided construit (test_source={source!r}). Ouvrir http://localhost:7474.")


if __name__ == "__main__":
    main()
