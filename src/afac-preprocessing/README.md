# AFAC Preprocessing Pipeline

Pipeline de prétraitement de documents PDF : OCR, enrichissement VLM (Qwen 3.5), export Markdown et génération de métadonnées.

---

## Prérequis système

- Python >= 3.11
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/) installé
- Accès au VLM (URL + certificat SSL fournis par Kieran)

---

## Installation

```bash
# 1. Cloner le dépôt
git clone <url-du-repo>
cd preprocessing

# 2. Installer les dépendances (lit uv.lock — versions exactes)
cd src/afac-preprocessing
uv sync
```

> `uv sync` utilise le certificat système automatiquement (`system-certs = true` dans `pyproject.toml`).  
> Pas besoin de créer un venv manuellement.

---

## Configuration

### Fichier d'environnement

```bash
cp .env.example .env.test   # pour les tests
# ou
cp .env.example .env        # pour la production
```

Ouvrir le fichier copié et renseigner les valeurs :

| Variable | Description |
|---|---|
| `VLM_URL` | URL du endpoint VLM (voir Kieran) |
| `VLM_MODEL_NAME` | Nom du modèle VLM (ex. `Qwen/Qwen3.5-122B-A10B-FP8`) |
| `VLM_CA_PEM` | Chemin vers le certificat SSL CA (fourni par Kieran) |
| `DOC_NAME` | Nom du PDF à traiter, sans extension (ex. `Annulation et retaxation`) |
| `ENABLE_IMAGE_DESCRIPTION` | `true` / `false` — activer la description d'images via VLM |
| `GEN_ID` | Optionnel — suffixe de version pour comparer plusieurs générations |

Le fichier actif est choisi dans `manifests/runtime.yaml` → `environment.dotenv_file`.

### Données d'entrée

Déposer les PDFs à traiter dans :
```
src/afac-preprocessing/data/input_files/
```

Pour le stage 5 (métadonnées), placer les PDFs dans la hiérarchie de dossiers :
```
src/afac-preprocessing/metadata/folder_source/<Thème>/<fichier>.pdf
# ex. : metadata/folder_source/Taxation/Annulation et retaxation.pdf
```

---

## Lancer le pipeline

```bash
# Pipeline complet sur un PDF (y compris dans un sous-dossier de data/input_files/)
uv run python pipeline_modular/automate_pipeline_example/fullpipeline_modular_v2.py \
  --dotenv .env.test --input "data/input_files/Adhésion/Demande prématurée.pdf"

# Pipeline complet sur TOUS les PDFs de data/input_files/ (sous-dossiers inclus)
uv run python pipeline_modular/automate_pipeline_example/batch_pipeline_all_pdfs.py \
  --dotenv .env.test

# Aperçu des PDFs qui seraient traités (sans exécution)
uv run python pipeline_modular/automate_pipeline_example/batch_pipeline_all_pdfs.py \
  --dotenv .env.test --dry-run
```

Consulter `pipeline_modular/README.md` pour la référence complète des paramètres et de chaque étape.

---

## Pense-bête

- `Ctrl + §` — commenter multi-ligne dans VS Code
- Faire un `git pull` le matin pour éviter les conflits entre branches


