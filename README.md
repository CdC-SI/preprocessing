# AFAC Preprocessing

Pipeline de prétraitement de documents PDF : extraction Docling/OCR,
enrichissement VLM (descriptions d'images, correction d'URLs, contrôle du
markdown), génération de métadonnées + embeddings pour le retrieval.

## Le pipeline en un coup d'œil

> **Diagramme :** [`docs/pipeline-overview.mmd`](docs/pipeline-overview.mmd)
> — les quatre phases, du PDF au CSV.

Un PDF entre, une ligne `CONTENT | METADATA | EMBEDDING` en sort. Les 13 étapes
se répartissent en quatre phases : extraction Docling (doctags + tables +
images), enrichissement par le VLM (descriptions d'images, correction des URLs),
conversion et contrôle du markdown, puis métadonnées et embeddings. **🤖 marque
les cinq étapes qui appellent un modèle** — ce sont elles qui déterminent la
durée d'un run.

Deux points de lecture : le PDF n'est pas consommé une fois pour toutes, six
étapes le rouvrent (traits pointillés) pour rendre les pages ou lire les liens
natifs ; et une 13ᵉ étape ne clôt pas le travail — l'agrégat `corpus.csv` est
reconstruit **après** le lot, quand tous les documents ont écrit leur CSV.
L'étape 03 (`opencv-check`, QA visuelle) est omise ici : elle est désactivée par
défaut. Le graphe détaillé, avec les fichiers échangés et les chemins de repli
des profils, est dans [docs/architecture.md](docs/architecture.md).

## Prérequis

- Python ≥ 3.11 et [uv](https://docs.astral.sh/uv/)
- L'URL d'un endpoint VLM (et d'embedding) — voir l'équipe infra

## Démarrage en 3 commandes

```bash
cp .env.example .env        # puis renseigner VLM_URL (et EMBEDDING_URL)
uv sync
uv run afac-preprocess doctor    # vérifie la config et dit quoi corriger
```

Déposez vos PDF sous `data/input_files/`, en respectant la convention
`<corpus>/<thème>/<document>.pdf` — c'est cette arborescence que la sortie
reproduira :

```
data/input_files/
└── afac/                    ← corpus (dossier racine)
    ├── Adhésion/            ← thème
    │   ├── Mineur.pdf
    │   └── Globe-trotter.pdf
    └── Taxation/
        └── Dispense.pdf
```

Un corpus à plat (`data/input_files/mesdocs/*.pdf`) fonctionne aussi ; seuls
`parent_label` / `children_label` seront vides dans les métadonnées.

## 1. Lancer le pipeline

`--input` accepte indifféremment un PDF ou un dossier, exploré récursivement.

```bash
# un document précis
uv run afac-preprocess run --input "data/input_files/afac/Adhésion/Mineur.pdf"

# un thème (tous les PDF du dossier et de ses sous-dossiers)
uv run afac-preprocess run --input "data/input_files/afac/Adhésion"

# tout le corpus
uv run afac-preprocess run --input data/input_files/afac

# absolument tout ce qui est présent
uv run afac-preprocess run --input data/input_files
```

Les 13 étapes s'enchaînent par document ; un échec isolé n'arrête pas le lot.
Prévoyez du temps : chaque document coûte plusieurs appels VLM (une description
par image, un contrôle par page), donc la durée suit le **volume** des documents,
pas leur nombre. Comptez plusieurs heures pour un corpus de 100+ PDF.

```bash
uv run afac-preprocess run --input data/input_files/afac --dry-run   # liste sans rien exécuter
uv run afac-preprocess run --input <…> --profile no-images           # sauter les descriptions d'images
uv run afac-preprocess run --input <…> --from-step markdown-convert  # reprendre après un échec
```

## Où atterrissent les sorties

```
data/output_files_preprocessing/<corpus>/<thème>/<nom-du-document>/
├── <doc>.doctags, <doc>.md, <doc>_final.md, …   # artefacts d'extraction
├── tables/                                       # tables CSV/HTML/JSONL
├── used_images/                                  # images extraites
└── metadata/<doc>_final.csv                      # CONTENT | METADATA | EMBEDDING

data/output_files_preprocessing/<corpus>/<corpus>.csv     # CSV global : une ligne par document
```

La sortie reproduit l'arborescence de `data/input_files/`, et chaque corpus
(dossier racine) reçoit un CSV global concaténant les CSV de ses documents.

Contrôle rapide que rien n'a échoué en silence :

```bash
uv run python tools/audit_pipeline_output.py --stage5 data/output_files_preprocessing
```

## 2. Comparer à la baseline docling et mesurer le retrieval

Le pipeline enrichit le markdown ; reste à savoir si ça **améliore** le
retrieval. Le protocole compare deux représentations des mêmes documents —
`<doc>.md` (docling brut) contre `<doc>_final.md` (enrichi) — évaluées avec
**les mêmes questions HyQ** et les mêmes métriques (Recall / Precision / nDCG /
MRR @ k). Les questions sont celles produites à l'étape 1, jamais régénérées :
seule la représentation du document change.

Prérequis : l'étape 1 est passée jusqu'au bout, donc chaque document a un
`metadata/hyq.json` et des `metadata/hyq_<doc>/question_*.csv`. Il faut aussi
**deux** extras — `eval` pour scikit-learn (les métriques) et `viz` pour
matplotlib (les graphiques du rapport) :

```bash
uv sync --extra eval --extra viz
```

Les trois commandes s'enchaînent dans cet ordre, depuis la racine du dépôt :

```bash
# a. Baseline : embedde le markdown docling brut de chaque document
uv run python -m afac_preprocessing.pipeline_baseline.single_docling_baseline

# b. Évaluation du pipeline enrichi, mêmes questions, mêmes métriques
uv run python -m afac_preprocessing.retrieval_protocol_evaluation.evaluate_all_docs

# c. Rapport de comparaison (delta par métrique et par k)
uv run python -m afac_preprocessing.pipeline_baseline.compare_baseline_report
```

**Variante HTML sans VLM.** Même calcul, mêmes chiffres (les fonctions
d'agrégation sont partagées), mais la sortie est une page autoportante et le
verdict est *calculé* — le signe du delta nDCG moyen — au lieu d'être rédigé
par un modèle :

```bash
uv run python -m afac_preprocessing.pipeline_baseline.comparison_report_html
```

Elle est reproductible au bit près (le verdict VLM, lui, varie d'un run à
l'autre : pas de cache, contrainte C1) et ne demande **pas** l'extra `viz` —
les graphiques sont du SVG écrit à la main, pas du matplotlib. Sortie :
`data/baseline_evaluation/comparison_report.html`, à ouvrir par double-clic.

Ces scripts découvrent **automatiquement** tous les documents exploitables sous
`data/output_files_preprocessing/`, à n'importe quelle profondeur. Pour
restreindre le périmètre, pointez `--stage5` sur un sous-dossier :

```bash
# un seul thème
uv run python -m afac_preprocessing.pipeline_baseline.single_docling_baseline \
    --stage5 "data/output_files_preprocessing/afac/Adhésion"

# un seul document : rapport de prévisualisation (après l'étape a, qui produit
# baseline_metadata.csv)
uv run python -m afac_preprocessing.pipeline_baseline.single_doc_preview_report --doc-name Mineur
```

**Les métriques de retrieval classent chaque question contre tout le
corpus** — elles n'ont de sens qu'à partir d'une dizaine de documents. Sur un
document isolé il n'y a rien à classer (`ndcg_score` refuse d'ailleurs de
calculer sur un seul candidat) : c'est précisément pourquoi
`single_doc_preview_report` existe et se limite à une comparaison qualitative.

Résultats produits :

| Fichier | Contenu |
|---|---|
| `data/baseline_evaluation/baseline_metadata.csv` | une ligne par doc : CONTENT / METADATA / HYQ / EMBEDDING de la baseline |
| `data/baseline_evaluation/baseline_results.csv` | une ligne par (doc, question), métriques @k |
| `data/pipeline_evaluation/global_summary.csv` | moyennes par document, côté pipeline |
| `data/baseline_evaluation/comparison_report.md` | **le rapport final** (markdown + verdict VLM) + graphiques dans `charts/` |
| `data/baseline_evaluation/comparison_report.html` | le même, en page autoportante **sans VLM** |
| `data/output_files_baseline/<doc>/<doc>.md` | copie du markdown brut évalué, pour inspection |

Lecture du rapport : `delta = baseline − pipeline`. **Positif ⇒ la baseline fait
mieux, donc le prétraitement dégrade le retrieval.** Négatif ⇒ le prétraitement
améliore. Le détail par document est trié par delta nDCG croissant : les
documents les plus dégradés apparaissent en haut.

Un appel VLM optionnel ajoute un verdict rédigé en tête du rapport —
`--no-vlm-analysis` pour s'en passer. Le protocole détaillé, avec les pièges
déjà rencontrés, est dans
[pipeline_baseline/protocole.md](src/afac_preprocessing/pipeline_baseline/protocole.md).

## Commandes utiles

```bash
uv run afac-preprocess steps            # liste des 13 étapes
uv run afac-preprocess steps --graph    # qui dépend de quoi
uv run afac-preprocess doctor           # diagnostique l'installation et dit quoi corriger
uv run afac-preprocess aggregate        # reconstruit les CSV globaux (fait aussi en fin de batch)
uv run afac-preprocess run --help       # profils : default, full, no-images, no-vlm, extract
```

## Extras optionnels

Le noyau installe uniquement le pipeline. Pour les autres chantiers du dépôt :

```bash
uv sync --extra kg      # Neo4j / GraphRAG / spaCy
uv sync --extra viz     # matplotlib / pyvis / networkx
uv sync --extra eval    # scikit-learn (évaluation retrieval)
uv sync --all-extras    # tout
```

## Documentation

- [docs/architecture.md](docs/architecture.md) — les 13 étapes, le contrat `PipelineStep`, les règles du noyau
- [docs/cli-options.md](docs/cli-options.md) — **toutes** les options des trois familles d'exécutables, les variables d'environnement, les recettes
- [CONTRIBUTING.md](CONTRIBUTING.md) — ajouter une étape, lancer les vérifications

Les diagrammes sont des fichiers Mermaid autonomes, dans `docs/` :

| Fichier | Contenu |
|---|---|
| [docs/pipeline-overview.mmd](docs/pipeline-overview.mmd) | les quatre phases, vue condensée |
| [docs/pipeline-steps.mmd](docs/pipeline-steps.mmd) | le DAG des 13 étapes, fichier par fichier |
| [docs/core-objects.mmd](docs/core-objects.mmd) | les objets du noyau et leur assemblage |

Ils s'ouvrent dans l'aperçu Mermaid de VS Code, sur
[mermaid.live](https://mermaid.live), ou s'exportent en image :

```bash
npx -y @mermaid-js/mermaid-cli -i docs/pipeline-steps.mmd -o docs/pipeline-steps.svg
```

## Outils hors pipeline

Scripts autonomes, à lancer séparément (chacun a son `--help`) :

```bash
uv run python tools/audit_pipeline_output.py --stage5 data/output_files_preprocessing
uv run python tools/markdown_tables_to_jsonl.py --markdown <doc>_final.md --embed-output <doc>_final_embed.md
uv run python tools/compare_outputs.py <référence> <sortie>
```

- `audit_pipeline_output.py` — contrôle santé read-only d'un arbre de sortie : détecte les étapes ayant échoué silencieusement.
- `markdown_tables_to_jsonl.py` — exporte les tables Markdown d'un `_final.md` en JSONL. Avec `--embed-output`, produit le `_final_embed.md` que le pipeline préfère comme CONTENT et comme source d'embedding s'il existe.
- `compare_outputs.py` — compare deux arbres de sortie (STRICT / STRUCTUREL / TOLERANT).
