# AFAC Preprocessing

Pipeline modulaire de prétraitement de documents PDF pour AFAC : OCR/extraction Docling,
enrichissement VLM (descriptions d'images, correction, structuration), export Markdown et
génération de métadonnées + embeddings pour le retrieval.

Chaque étape est un script autonome, orchestrable individuellement ou via le pipeline complet.

## Démarrage rapide

```bash
git clone <url-du-repo>
cd preprocessing
uv sync
cd src/afac-preprocessing
uv run python pipeline_preprocessing/orchestrators/pipeline_extraction.py \
  --dotenv .env.test --input "data/input_files/afac/Adhésion/MonDoc.pdf"
```

Voir [src/afac-preprocessing/README.md](src/afac-preprocessing/README.md) pour l'installation,
la configuration (`.env`, VLM) et les commandes de lancement complètes.

## Documentation

- [src/afac-preprocessing/README.md](src/afac-preprocessing/README.md) — installation, configuration, quickstart
- [pipeline_preprocessing/README.md](src/afac-preprocessing/pipeline_preprocessing/README.md) — référence des 13 étapes
- [pipeline_preprocessing/orchestrators/README.md](src/afac-preprocessing/pipeline_preprocessing/orchestrators/README.md) — orchestrateurs (pipeline complet, batch)
- [pipeline_baseline/README.md](src/afac-preprocessing/pipeline_baseline/README.md) — évaluation baseline vs pipeline
- [retrieval_protocol_evaluation/README.md](src/afac-preprocessing/retrieval_protocol_evaluation/README.md) — protocole d'évaluation retrieval
