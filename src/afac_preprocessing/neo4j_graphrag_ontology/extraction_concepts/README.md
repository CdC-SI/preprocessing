# Extraction de concepts & graphe concept-guided (AFAC)

Ticket Jira 548 : extraire les concepts métier des documents AFAC (mots-clés + extraction libre
par VLM), puis s'en servir comme vocabulaire fermé pour construire un knowledge graph Neo4j où
les prédicats restent libres — en alternative à l'ontologie fermée de `graphrag/`.

Périmètre actuel : **thème Adhésion uniquement** (20 documents déjà prétraités en `_final.md`
dans `data/output_files_preprocessing/`).

**Prérequis** : le container `neo4j-afac` démarré (voir [`../README.md`](../README.md)) et
`.env.test` présent — tous les scripts ci-dessous se lancent depuis
`preprocessing/`.

---

## Démarrage rapide

Trois étapes dans les deux cas : **extraire les concepts** → **construire le graphe** →
**poser une question**.

### Exemple sur un seul document (`Mineur`)

```bash
cd preprocessing

# 1. Extraire les concepts de ce document (mots-clés + VLM)
uv run --active python neo4j_graphrag_ontology/extraction_concepts/extract_doc_concepts.py \
    --doc-name Mineur --dotenv .env.test

# 2. Construire le graphe pour ce document (thème Adhésion)
uv run --active python neo4j_graphrag_ontology/extraction_concepts/build_kg_from_concepts.py \
    --theme Adhésion --doc-name Mineur --dotenv .env.test

# 3. Poser une question sur ce graphe
uv run --active python neo4j_graphrag_ontology/extraction_concepts/test_graphrag_question.py \
    --theme Adhésion --dotenv .env.test --question "GEDO sert à quoi ?"
```

L'étape 2 fait aussi le typage du vocabulaire du thème (mis en cache), donc les lancements
suivants — y compris le batch complet ci-dessous — ne rappellent pas le VLM pour ça.

### Batch complet (20 documents du thème Adhésion)

```bash
cd preprocessing

# 1. Extraire les concepts de tous les documents (fit TF-IDF une fois, puis boucle)
uv run --active python neo4j_graphrag_ontology/extraction_concepts/batch_extract_concepts.py \
    --dotenv .env.test

# 1bis. Agréger les concepts au niveau thème (nécessaire avant l'étape 2)
uv run --active python neo4j_graphrag_ontology/extraction_concepts/aggregate_theme_concepts.py

# 2. Construire le graphe pour les 20 documents
uv run --active python neo4j_graphrag_ontology/extraction_concepts/build_kg_from_concepts.py \
    --theme Adhésion --dotenv .env.test

# 3. Poser une question sur le graphe complet
uv run --active python neo4j_graphrag_ontology/extraction_concepts/test_graphrag_question.py \
    --theme Adhésion --dotenv .env.test
```

Le graphe est isolé sous `test_source="concept_kg_adhésion"` — visualiser dans Neo4j Browser
(`http://localhost:7474`) :

```cypher
MATCH (n)-[r {test_source: "concept_kg_adhésion"}]->(m)
WHERE type(r) <> "FROM_CHUNK" RETURN n, r, m
```

**Pour repartir de zéro** (étape 2) :

```bash
uv run --active python neo4j_graphrag_ontology/extraction_concepts/build_kg_from_concepts.py \
    --theme Adhésion --wipe --dotenv .env.test
```

### Options utiles (étape 2 — `build_kg_from_concepts.py`)

| Option | Effet |
| :--- | :--- |
| `--doc-name <nom>` (répétable) | Limite le batch à ces documents. |
| `--retype` | Force le recalcul du glossaire de types (ignore le cache). |
| `--no-embeddings` | Ne pas embedder les chunks (pas d'index vectoriel). |
| `--index-only` | Ne (re)crée que l'index vectoriel, sans relancer l'extraction. |
| `--wipe` | Supprime le graphe de ce thème, puis quitte. |

### Options utiles (étape 3 — `test_graphrag_question.py`)

`--question "..."` pour poser une autre question que celle par défaut. Chaque run est
sauvegardé dans `qa_runs/<horodatage>_<slug>.md` (contexte, sources, chunks, réponse) — rien
n'est écrasé, pratique pour comparer plusieurs questions.

---

## Référence détaillée

### Les deux méthodes d'extraction de concepts

Chaque document est analysé par **deux méthodes complémentaires**, gardées séparées dans la
sortie plutôt que fusionnées (pas d'algorithme de matching flou entre les deux) — les champs
JSON portent la méthode d'origine en préfixe pour lever toute ambiguïté à la lecture :

1. **`spacy_keywords`** (TF-IDF, **uni- et bigrammes**) — statistique, sur l'ensemble du corpus
   des 20 docs. Les bigrammes (ex. "confirmation adhésion") rendent la comparaison avec le VLM
   *like-for-like* : sans eux, un vocabulaire d'unigrammes seuls ne peut structurellement pas
   s'accorder avec des concepts multi-mots. Une petite stoplist domaine (`_DOMAIN_STOPWORDS`)
   filtre le bruit récurrent des tableaux d'historique de version (`gt`, `am`, `corres`).
2. **`vlm_concepts`** (VLM thinking) — sémantique, extraction libre guidée par prompt
   (`vlm_concepts_raw` garde la trace brute avant nettoyage).

Une passe de comparaison (`compare_concepts.py`) réconcilie les deux signaux **par document**
en 3 seaux (`agreement` / `vlm_only` / `stat_only`, cf. `ConceptComparison`), puis une passe
d'agrégation (`aggregate_theme_concepts.py`) fait remonter mots-clés, concepts **et**
comparaison au niveau **thème**.

### Scripts

| Script | Rôle |
| :--- | :--- |
| [`schema.py`](schema.py) | Modèles Pydantic partagés : `Keyword`, `DocConcepts` (sortie par document — `spacy_keywords`, `vlm_concepts_raw`, `vlm_concepts`, `comparison`), `ConceptComparison` (3 seaux `agreement`/`vlm_only`/`stat_only`, cf. `compare_concepts.py`), `ThemeKeywordRow`, `ThemeConcepts` (sortie par thème — rollup VLM **et** spaCy **et** comparaison). Pas de logique d'exécution — juste les structures de données et `normalize_concepts()`. |
| [`keyword_extraction.py`](keyword_extraction.py) | Classe `KeywordExtractor`. **Pas de lemmatisation** (retour d'équipe : peu fiable sur ce corpus, cf. `CORRES` → lemme inexistant "corre") — forme de surface (`tok.text.lower()`) filtrée par POS spaCy (`fr_core_news_lg`, garde uniquement NOUN/PROPN/ADJ), stopwords, ponctuation, bruit domaine `_DOMAIN_STOPWORDS` et tokens numériques (`_surface_terms()`), puis construit **uni- et bigrammes** (`_terms()`) avant de fit un `TfidfVectorizer` sur l'ensemble du corpus (le score TF-IDF n'a de sens que relatif à un corpus). Pour chaque terme retourne `count`, `score` (TF-IDF) et `doc_freq` → alimente `DocConcepts.spacy_keywords`. |
| [`concept_extraction_llm.py`](concept_extraction_llm.py) | Classe `ConceptLLMExtractor`. Extraction libre de concepts métier via VLM avec **thinking activé** (`utils.vlm_client.text_completion_thinking`), guidée par une définition générique écrite dans le prompt (**pas** par `ontology/afac_ontology.py` — cette ontologie a été construite sur ce même corpus et normaliser/guider avec serait circulaire). Sortie JSON `{"concepts": [...]}` parsée de façon tolérante. |
| [`extract_doc_concepts.py`](extract_doc_concepts.py) | Classe `DocConceptsExtractor`. Combine les deux extracteurs ci-dessus pour **un seul document**, construit un `DocConcepts`, écrit `output/<doc>.json` + `output/<doc>.md`. |
| [`batch_extract_concepts.py`](batch_extract_concepts.py) | Classe `BatchConceptExtractor`. Fit le TF-IDF une seule fois sur tout le corpus puis boucle sur les 20 documents. Un échec sur un document n'interrompt pas le batch. |
| [`compare_concepts.py`](compare_concepts.py) | Classe `ConceptComparator`. Compare `vlm_concepts` (VLM) et `spacy_keywords` (TF-IDF, uni+bigrammes) d'un document, matching **like-for-like** : un concept multi-mots n'est en accord que si un **bigramme entier** le corrobore. Répartit en 3 seaux : `agreement`, `vlm_only`, `stat_only`. Appelé automatiquement par `extract_doc_concepts.py` ; utilisable aussi en standalone. |
| [`aggregate_theme_concepts.py`](aggregate_theme_concepts.py) | Classe `ThemeConceptAggregator`. Relit tous les `output/*.json`, regroupe par thème. Fait remonter 3 vues supplémentaires au niveau thème : `keyword_rollup`, `theme_agreement`, `theme_vlm_only`/`theme_stat_only`. Écrit `output/_theme_<Thème>.json` + `.md`. |
| [`build_kg_from_concepts.py`](build_kg_from_concepts.py) | Construit le graphe pour les documents d'un thème, en se rapprochant du comportement de `graphrag/batch_build_kg.py` tout en gardant les prédicats libres : `ThemeVocabulary`/`ConceptTyper` typent chaque concept **une seule fois au niveau thème** ; `EntityNameNormalizer` canonicalise le nom avant le chargement ; `ConceptGraphLoader` fusionne sur un label d'ancrage stable `ConceptTest` **jamais retiré**, donc un concept partagé entre documents (GEDO, SITAX...) fusionne automatiquement sur tout le batch ; `ConceptRelationExtractor` garde les prédicats 100% libres, groupés **par chunk** (`ChunkExtraction`) pour connaître le passage exact d'origine de chaque relation ; `LexicalGraphLoader` crée un vrai graphe lexical (`TextSource`/`TextChunk`, convention `neo4j-graphrag`), embedding compris (index vectoriel officiel en fin de batch). Isolé via `test_source="concept_kg_<thème>"`. |
| [`test_graphrag_question.py`](test_graphrag_question.py) | Test GraphRAG **asynchrone** sur une question (fixe par défaut ou `--question`) : `GraphContextRetriever` relit le sous-graphe comme contexte (annoté du document source de chaque relation), le VLM répond en **citant sa source** (`AsyncGraphRAGAnswerer`). `SourceCitationResolver` résout chaque document vers son fichier réel. `ChunkVectorValidator` interroge en plus l'index vectoriel (top-k chunks les plus proches, second angle de validation). `AnswerRecorder` sauvegarde chaque run dans `qa_runs/`. |

`utils/vlm_client.py` a aussi reçu `text_completion_thinking()` et `text_completion_async()` —
sœurs de `text_completion()`, aucune fonction existante n'a été modifiée.

`shared/kg_shared_utils.py` et `shared/extraction_vlm_common.py` regroupent les utilitaires
communs à ce pipeline et à `graphrag/` (`EmbedderFactory`, `GraphNormalizer`, `DocumentLocator`,
`TextChunker`, `TolerantJsonParser`).

### Sorties

Tout est écrit dans [`output/`](output/) :
- `<doc>.json` / `<doc>.md` — un par document (`spacy_keywords`, `vlm_concepts`, `comparison` à 3 seaux).
- `_theme_<Thème>.json` / `.md` — rollup par thème.
- `_theme_<Thème>_glossary.json` — cache du glossaire de types (`build_kg_from_concepts.py`).

Les réponses GraphRAG sont écrites dans [`qa_runs/`](qa_runs/) (une par run, nom horodaté).

### Autres commandes de lecture seule

```bash
# Comparer concepts (VLM) et mots-clés (TF-IDF) sur un document déjà extrait
uv run --active python neo4j_graphrag_ontology/extraction_concepts/compare_concepts.py --doc-name Mineur

# Sanity check des mots-clés seuls (hors-ligne, pas d'appel VLM)
uv run --active python neo4j_graphrag_ontology/extraction_concepts/keyword_extraction.py
```
