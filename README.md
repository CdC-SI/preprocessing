# AFAC Preprocessing

Pipeline de prétraitement de documents PDF : extraction Docling/OCR,
enrichissement VLM (descriptions d'images, correction d'URLs, contrôle du
markdown), génération de métadonnées + embeddings pour le retrieval.

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

⚠️ **Les métriques de retrieval classent chaque question contre tout le
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
| `data/baseline_evaluation/comparison_report.md` | **le rapport final** + graphiques dans `charts/` |
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
- [CONTRIBUTING.md](CONTRIBUTING.md) — ajouter une étape, lancer les vérifications

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
