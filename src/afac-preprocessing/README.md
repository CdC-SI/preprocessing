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
uv sync
```

> `uv sync` utilise le certificat système automatiquement (`system-certs = true` dans `pyproject.toml`).  
> Pas besoin de créer un venv manuellement.

---

## Configuration

### Fichier d'environnement

```bash
cp ../../.env.test   # pour les tests
# ou
cp ../../.env        # pour la production
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

Déposer les PDFs à traiter dans la hiérarchie `<source>/<thème>/[<sous-thème>/]<fichier>.pdf` :
```
src/afac-preprocessing/data/input_files/<source>/<thème>/<fichier>.pdf
# ex. : data/input_files/afac/Taxation/Annulation et retaxation.pdf
```
Cette même hiérarchie sert aussi de référence pour le stage 5 (métadonnées : source,
dossier parent, documents frères) — pas de dossier miroir séparé à maintenir.

---

## Lancer le pipeline

```bash
# Pipeline complet sur un PDF (y compris dans un sous-dossier de data/input_files/)
uv run python pipeline_modular/automate_pipeline_example/pipeline_extraction.py \
  --dotenv .env.test --input "data/input_files/afac/Adhésion/Demande prématurée.pdf"

# Pipeline complet sur TOUS les PDFs de data/input_files/ (sous-dossiers inclus)
uv run python pipeline_modular/automate_pipeline_example/batch_pipeline_all_pdfs.py \
  --dotenv .env.test

# Aperçu des PDFs qui seraient traités (sans exécution)
uv run python pipeline_modular/automate_pipeline_example/batch_pipeline_all_pdfs.py \
  --dotenv .env.test --dry-run
```

Consulter `pipeline_modular/README.md` pour la référence complète des paramètres et de chaque étape.

---

## Tester la solution : baseline (docling brut) vs pipeline de prétraitement

Pour vérifier que le prétraitement (OCR, enrichissement VLM, structuration) apporte
réellement quelque chose, on compare deux représentations d'un même corpus, évaluées avec
**les mêmes questions HyQ** et **les mêmes métriques** (Recall/Precision/nDCG/MRR@k) :
- **baseline** : markdown produit directement par Docling, sans aucun enrichissement.
- **pipeline** : markdown enrichi (v2, ou v3 — tables natives + descriptions d'images).

Le delta mesure directement l'apport (ou le coût) du prétraitement. Toutes les commandes
ci-dessous s'exécutent depuis `src/afac-preprocessing/` et supposent un `.env.test` déjà
configuré (cf. Configuration ci-dessus).

### 1. Générer le pipeline de prétraitement sur un corpus

```bash
uv run python pipeline_modular/automate_pipeline_example/batch_pipeline_all_pdfs.py \
  --dotenv .env.test --input-dir "data/input_files/afac/Adhésion"
```

Boucle sur chaque PDF trouvé et lance `pipeline_extraction.py` (13 étapes). Si un
document échoue, le batch continue et liste les échecs à la fin ; rejouer un seul document
après correction avec `--input <pdf> --from-step N` (numéro de la première étape en échec).

### 2. Générer la baseline (docling brut) à partir des mêmes documents

```bash
uv run python single_retrieval_nopreprocessing/single_docling_baseline.py --dotenv .env.test
```

Embedde le markdown brut de chaque document déjà traité à l'étape 1 ; réutilise telles
quelles les questions HyQ déjà générées (jamais régénérées, pour comparer à questions
identiques). Sorties : `data/baseline_evaluation/baseline_metadata.csv` et
`baseline_results.csv`.

### 3. Évaluer le pipeline avec les mêmes métriques

```bash
uv run python retrieval_protocol_evaluation/evaluate_all_docs.py
```

Sortie : `data/evaluation_results/global_summary.csv`.

### 4. Générer le rapport de comparaison

```bash
uv run python single_retrieval_nopreprocessing/compare_baseline_report.py --dotenv .env.test
```

Fusionne les deux évaluations, calcule le delta par métrique@k (`delta = baseline −
pipeline` : positif ⇒ la baseline fait mieux, le prétraitement dégrade ; négatif ⇒ le
prétraitement améliore), et génère `data/baseline_evaluation/comparison_report.md` +
graphiques. Un verdict VLM optionnel (désactivable avec `--no-vlm-analysis`) résume le
rapport en tête de fichier.

Pour comparer aussi la variante v3 (tables markdown natives, descriptions d'images
activées par défaut — voir `pipeline_modular/automate_pipeline_example/README.md`) ou tester
un seul document avant de relancer tout le corpus, voir
`single_retrieval_nopreprocessing/README.md`.

---

## Pense-bête

- `Ctrl + §` — commenter multi-ligne dans VS Code
- Faire un `git pull` le matin pour éviter les conflits entre branches


