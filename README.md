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
uv run afac-preprocess run --input "data/input_files/afac/Adhésion/Mineur.pdf"
```

`--input` accepte un PDF **ou un dossier** (exploré récursivement) :

```bash
uv run afac-preprocess run --input data/input_files/afac/Adhésion --profile no-images
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
