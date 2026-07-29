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
data/output_files_preprocessing/<nom-du-document>/
├── <doc>.doctags, <doc>.md, <doc>_final.md, …   # artefacts d'extraction
├── tables/                                       # tables CSV/HTML/JSONL
├── used_images/                                  # images extraites
└── metadata/<doc>_final.csv                      # CONTENT | METADATA | EMBEDDING
```

## Commandes utiles

```bash
uv run afac-preprocess steps            # liste des 13 étapes
uv run afac-preprocess steps --graph    # qui dépend de quoi
uv run afac-preprocess doctor           # diagnostique l'installation et dit quoi corriger
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

- [docs/README-details.md](docs/README-details.md) — anciens points d'entrée et liens détaillés
- [src/afac_preprocessing/pipeline_preprocessing/README.md](src/afac_preprocessing/pipeline_preprocessing/README.md) — référence des 13 étapes
