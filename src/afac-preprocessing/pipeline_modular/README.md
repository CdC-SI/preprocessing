# Pipeline Modular — Référence CLI

Pipeline de prétraitement PDF en 13 étapes : extraction Docling, enrichissement VLM, génération de métadonnées et embeddings.

## Vue d'ensemble des étapes

| # | Script | Rôle | Entrée | Sortie |
|---|--------|------|--------|--------|
| 01 | `pipeline_multietape_modular.py` | Extraction Docling + export images PNG *(optionnel)* | PDF | `.doctags` `.json` `.md` `.txt` · `used_images/` |
| 02 | `reordered_doctags_modular.py` | Réordonnancement des blocs | `.doctags` | `_reordered.doctags` |
| 03 | `opencv_checker_modular.py` | Validation visuelle *(optionnel)* | PDF + `.doctags` | PNG par page |
| 04 | `csv_to_jsonlines_modular.py` | Conversion tables CSV → JSONL | `tables/*.csv` | `tables/*.jsonl` |
| 05 | `load_jsonline_doctags_modular.py` | Injection tables dans doctags | `_reordered.doctags` + JSONL | `_reordered_with_tables.doctags` |
| 06 | `description_image_context_modular.py` | Descriptions images via VLM *(lent)* — émet des marqueurs `[[[IMAGE_DESC:N]]]` | `_reordered_with_tables.doctags` + PDF | `_reordered_with_tables_pictures.doctags` + `_image_descriptions.md` |
| 07 | `url_extaction_modular.py` | Extraction liens hypertextes | PDF | `hyperlinks_data_<doc>.jsonl` |
| 08 | `url_tuning_vlm_modular.py` | Intégration liens via VLM + corrections OCR | `_reordered_with_tables_pictures.doctags` + JSONL | `_url_vlm.doctags` |
| 09 | `docling_markdown_converter_modular.py` | Conversion doctags → Markdown paginé | `_url_vlm.doctags` | `_url_vlm.md` *(avec `<!-- page-break -->`)* |
| 10 | `markdown_control_vlm_modular.py` | Contrôle qualité, formatage et couleurs via VLM | `_url_vlm.md` + PDF | `_vlm_check.md` |
| 11 | `inject_image_descriptions_modular.py` | Injection des descriptions d'images dans le Markdown | `_vlm_check.md` + `_image_descriptions.md` | `_final.md` |
| 12 | `metadata_generation_modular.py` | Génération métadonnées + CSV final | Sorties stages 1–11 | `metadata/<doc>_final.csv` |
| 13 | `hyq_embedding_doc_modular.py` | Embeddings questions hyq | `metadata/hyq.json` | `metadata/hyq_<doc>/question_N.csv` |

**Sortie par document :**
```
data/output_files_preprocessing/<doc_name>/
├── <doc>.doctags / .json / .md / .txt           ← step 01
├── <doc>_reordered.doctags                      ← step 02
├── <doc>_reordered_with_tables.doctags          ← step 05
├── <doc>_reordered_with_tables_pictures.doctags ← step 06
├── <doc>_url_vlm.doctags                        ← step 08
├── <doc>_url_vlm.md                             ← step 09
├── <doc>_vlm_check.md                           ← step 10
├── <doc>_final.md                               ← step 11
├── hyperlinks_data_<doc>.jsonl                  ← step 07
├── <doc>_image_descriptions.md                  ← step 06
├── opencv_validation/                           ← step 03
├── tables/                                      ← steps 01, 04, 05
├── used_images/                                 ← step 01 (--extract-images) ou step 06 (fitz)
└── metadata/                                    ← steps 12, 13
    ├── resume.md
    ├── intent.json
    ├── hyq.json
    ├── embedding.json
    ├── <doc>_final.csv
    └── hyq_<doc>/
        ├── question_1.csv
        └── ...
```

---

# fullpipeline_modular_v2.py — Orchestrateur du pipeline complet

Lance les 13 étapes en séquence. Chaque étape reçoit le `--dotenv` résolu pour que `DOC_NAME` soit cohérent sur toute la durée du run.

## Commandes types

```bash
# Pipeline complet
uv run python pipeline_modular/automate_pipeline_example/fullpipeline_modular_v2.py --dotenv .env.test

# Reprendre après un échec à l'étape 8
uv run python pipeline_modular/automate_pipeline_example/fullpipeline_modular_v2.py --dotenv .env.test --from-step 8

# Seulement les métadonnées (étapes 12–13)
uv run python pipeline_modular/automate_pipeline_example/fullpipeline_modular_v2.py --dotenv .env.test --from-step 12

# Extraction seulement, sans injection ni métadonnées
uv run python pipeline_modular/automate_pipeline_example/fullpipeline_modular_v2.py --dotenv .env.test --to-step 10

# Ignorer opencv (étape 3) et descriptions images (étape 6, lente)
uv run python pipeline_modular/automate_pipeline_example/fullpipeline_modular_v2.py --dotenv .env.test --skip-steps 3,6
```

## Paramètres

| Paramètre | Défaut | Description |
|-----------|--------|-------------|
| `--dotenv` | `.env.test` | Fichier `.env` transmis à chaque étape. |
| `--input` | *(depuis .env)* | Chemin vers le PDF à traiter. Surcharge `DOC_NAME` et `DOC_PATH` du `.env`. Accepte les sous-dossiers de `data/input_files/`. |
| `--from-step` | `1` | Première étape à exécuter (1–13, inclus). |
| `--to-step` | `13` | Dernière étape à exécuter (1–13, inclus). |
| `--skip-steps` | *(aucun)* | Étapes à ignorer, séparées par virgule. Ex. `--skip-steps 3,6`. |

**Comportement en cas d'échec :** si une étape retourne un code ≠ 0, le pipeline s'arrête immédiatement et affiche `[FAILED] <script> exited with code N`. Les étapes suivantes ne sont pas exécutées.

**Note sur `--input` :** si absent, `DOC_NAME` et `DOC_PATH` doivent être renseignés dans le fichier `.env`. Avec `--input`, le pipeline calcule automatiquement le chemin relatif depuis `data/input_files/`, ce qui permet de pointer directement un PDF dans un sous-dossier (ex. `--input data/input_files/Adhésion/Demande prématurée.pdf`).

**Note sur `--skip-steps` :** ignorer une étape ne supprime pas la dépendance sur ses fichiers de sortie. Si l'étape 1 est ignorée mais que `<doc>.doctags` n'existe pas, l'étape 2 échouera avec un message d'erreur explicite (fichier introuvable).

---

# batch_pipeline_all_pdfs.py — Traitement automatique de tous les PDFs

Parcourt récursivement `data/input_files/` et lance `fullpipeline_modular_v2.py` sur chaque PDF trouvé, y compris dans les sous-dossiers. Les PDFs sont traités **séquentiellement**. En cas d'échec sur un document, le batch continue sur les suivants et liste tous les échecs en fin d'exécution.

## Commandes types

```bash
# Traiter tous les PDFs
uv run python pipeline_modular/automate_pipeline_example/batch_pipeline_all_pdfs.py --dotenv .env.test

# Aperçu des PDFs qui seraient traités (sans exécution)
uv run python pipeline_modular/automate_pipeline_example/batch_pipeline_all_pdfs.py --dotenv .env.test --dry-run

# Reprendre à partir de l'étape 8 pour tous les PDFs
uv run python pipeline_modular/automate_pipeline_example/batch_pipeline_all_pdfs.py --dotenv .env.test --from-step 8

# Ignorer opencv et descriptions images (étapes lentes)
uv run python pipeline_modular/automate_pipeline_example/batch_pipeline_all_pdfs.py --dotenv .env.test --skip-steps 3,6
```

## Paramètres

| Paramètre | Défaut | Description |
|-----------|--------|-------------|
| `--dotenv` | `.env.test` | Fichier `.env` transmis à chaque étape de chaque document. |
| `--dry-run` | *(désactivé)* | Affiche la liste des PDFs détectés sans lancer le pipeline. |
| `--from-step` | `1` | Forwarded à `fullpipeline_modular_v2.py` — première étape à exécuter. |
| `--to-step` | `13` | Forwarded à `fullpipeline_modular_v2.py` — dernière étape à exécuter. |
| `--skip-steps` | *(aucun)* | Forwarded à `fullpipeline_modular_v2.py` — étapes à ignorer. |

**Résilience :** un échec sur un PDF n'interrompt pas le batch. Le script retourne `exit 1` uniquement si au moins un PDF a échoué, avec la liste des documents concernés.

---

# Script pipeline_multietape_modular.py — Extraction Docling

Script unifié qui remplace `pipeline_multietape.py` (stage 1) et `export_table_docling.py` (stage 2). Une seule conversion Docling produit tous les formats demandés.

Avec `--extract-images` (ou `ENABLE_IMAGE_EXTRACTION=true` dans le `.env`), Docling extrait aussi les images en PNG dans `used_images/`. Ces images sont utilisées par `description_image_context_modular.py` à l'étape 06, évitant un re-crop fitz.

## Commandes types

```bash
# Extraction texte + images PNG via Docling (recommandé avant étape 06)
uv run python pipeline_modular/simple_extraction/pipeline_multietape_modular.py \
  --dotenv .env.test --extract-images

# Commande complète avec tous les paramètres
uv run python pipeline_modular/simple_extraction/pipeline_multietape_modular.py \
  --input      data/input_files/MonDoc.pdf \
  --output-dir ./data/output_files_preprocessing/MonDoc \
  --formats    json md txt doctags \
  --lang       fr en \
  --threads    4 \
  --device     cuda \
  --no-tables \
  --extract-images \
  --images-scale 4.17 \
  --dotenv     .env.test

# Extraction texte seule (sans images)
uv run python pipeline_modular/simple_extraction/pipeline_multietape_modular.py --dotenv .env.test
```

## Variables d'environnement (.env.test)

| Variable | Défaut | Description |
|----------|--------|-------------|
| `DOC_NAME` | *(obligatoire)* | Nom du document sans `.pdf`. |
| `ENABLE_IMAGE_EXTRACTION` | `false` | `true` pour activer l'export PNG Docling sans passer `--extract-images`. |

## Paramètres

| Paramètre | Alias | Défaut | Description |
|-----------|-------|--------|-------------|
| `--input` | `-i` | *(voir note)* | Chemin vers le PDF à traiter. |
| `--output-dir` | `-o` | `data/output_files_preprocessing/<nom_doc>/` | Dossier de sortie. Créé automatiquement. |
| `--formats` | `-f` | tous | Formats parmi `json md txt doctags`. |
| `--lang` | `-l` | `fr` | Code(s) de langue EasyOCR. Ex. `fr en ar`. |
| `--threads` | `-t` | `4` | Threads CPU alloués à Docling. |
| `--device` | | `cuda` | Accélérateur : `cuda` ou `cpu`. |
| `--no-ocr` | | désactivé | Désactive EasyOCR. Utile pour les PDFs natifs. |
| `--no-tables` | | désactivé | Désactive la détection de tableaux. |
| `--extract-images` | | `false` | Active l'export PNG des images Docling. Prioritaire sur `ENABLE_IMAGE_EXTRACTION`. |
| `--images-scale` | | `2.0` *(≈ 144 DPI)* | Facteur d'échelle Docling (base 72 DPI). Ex. `2.08`≈150 DPI, `4.17`≈300 DPI. |
| `--images-dir` | | `used_images/` dans le dossier de sortie | Dossier de destination des PNG exportés. |
| `--dotenv` | | *(aucun)* | Fichier `.env` à charger. Ignoré si `--input` est fourni. |

*Note : `--input` absent → résout `data/input_files/<DOC_NAME>.pdf` depuis la variable `DOC_NAME`.*

## Formats de sortie

| Format | Fichier produit |
|--------|----------------|
| `json` | `<doc>.json` — structure complète Docling |
| `md` | `<doc>.md` — Markdown avec tableaux intégrés |
| `txt` | `<doc>.txt` — texte brut |
| `doctags` | `<doc>.doctags` — format DocTags Docling |
| `csv` | `tables/<doc>-table-N.csv` par tableau |
| `html` | `tables/<doc>-table-N.html` par tableau |
| PNG | `used_images/pic{N}_page{P}.png` *(si `--extract-images`)* |

---

# Script reordered_doctags_modular.py — Réordonnancement des blocs DocTags

Corrige l'ordre des blocs extraits par Docling dans un fichier `.doctags`. Trie par position verticale (y0) puis horizontale (x0), page par page.

## Commandes types

```bash
# Résolution auto depuis DOC_NAME
uv run python pipeline_modular/simple_extraction/reordered_doctags_modular.py --dotenv .env.test

# Chemin explicite
uv run python pipeline_modular/simple_extraction/reordered_doctags_modular.py \
  --input data/output_files_preprocessing/MonDoc/MonDoc.doctags
```

## Paramètres

| Paramètre | Alias | Défaut | Description |
|-----------|-------|--------|-------------|
| `--input` | `-i` | *(voir note)* | Fichier `.doctags` source. |
| `--output` | `-o` | `<même dossier>/<stem>_reordered.doctags` | Fichier de sortie. |
| `--dotenv` | | *(aucun)* | Fichier `.env` pour résoudre `DOC_NAME`. Ignoré si `--input` est fourni. |

*Note : `--input` absent → résout `data/output_files_preprocessing/<DOC_NAME>/<DOC_NAME>.doctags`.*

## Logique de tri

| Cas | Comportement |
|-----|-------------|
| Bloc avec `y0` | Trié par `y0` croissant, puis `x0` |
| Bloc sans `y0` | Conservé en tête de page dans son ordre d'origine |
| `<ordered_list>` | Traité comme un seul bloc |
| `<unordered_list>` | Items triés individuellement, réenveloppés après tri |

---

# Script opencv_checker_modular.py — Validation visuelle des DocTags

Superpose les bounding boxes Docling sur chaque page du PDF et exporte les images PNG. **Outil de validation uniquement** — les PNG ne sont pas utilisés par les étapes suivantes.

## Commandes types

```bash
# Résolution auto depuis DOC_NAME
uv run python pipeline_modular/simple_extraction/opencv_checker_modular.py --dotenv .env.test

# Chemins explicites
uv run python pipeline_modular/simple_extraction/opencv_checker_modular.py \
  --input    data/input_files/MonDoc.pdf \
  --doctags  data/output_files_preprocessing/MonDoc/MonDoc.doctags \
  --output-dir data/output_files_preprocessing/MonDoc/opencv_validation

# DPI réduit pour validation rapide
uv run python pipeline_modular/simple_extraction/opencv_checker_modular.py \
  --input data/input_files/MonDoc.pdf --dpi 150
```

## Paramètres

| Paramètre | Alias | Défaut | Description |
|-----------|-------|--------|-------------|
| `--input` | `-i` | *(voir note)* | Chemin vers le PDF source. |
| `--doctags` | `-d` | `data/output_files_preprocessing/<stem>/<stem>.doctags` | Fichier `.doctags`. |
| `--output-dir` | `-o` | `data/output_files_preprocessing/<stem>/opencv_validation/` | Dossier de sortie PNG. |
| `--dpi` | | `300` | Résolution de rendu. |
| `--dotenv` | | *(aucun)* | Fichier `.env` pour résoudre `DOC_NAME`. Ignoré si `--input` est fourni. |

*Note : `--input` absent → résout `data/input_files/<DOC_NAME>.pdf`.*

**Exit codes**

| Code | Explication |
|------|-------------|
| `0` | Traitement terminé (pages en erreur signalées en WARNING dans les logs — pipeline non interrompu) |

---

# Script csv_to_jsonlines_modular.py — Conversion tables CSV → JSONL

Convertit les fichiers CSV extraits par `pipeline_multietape_modular.py` en JSONL. Un `.jsonl` par CSV, une ligne JSON par ligne du tableau.

## Commandes types

```bash
uv run python pipeline_modular/simple_extraction/csv_to_jsonlines_modular.py --dotenv .env.test

uv run python pipeline_modular/simple_extraction/csv_to_jsonlines_modular.py \
  --input-dir data/output_files_preprocessing/MonDoc/tables
```

## Paramètres

| Paramètre | Alias | Défaut | Description |
|-----------|-------|--------|-------------|
| `--input-dir` | `-i` | *(voir note)* | Dossier contenant les `*.csv` à convertir. |
| `--output-dir` | `-o` | même dossier que `--input-dir` | Dossier de sortie pour les `.jsonl`. |
| `--dotenv` | | *(aucun)* | Fichier `.env` pour résoudre `DOC_NAME`. Ignoré si `--input-dir` est fourni. |

*Note : `--input-dir` absent → résout `data/output_files_preprocessing/<DOC_NAME>/tables/`.*

**Détection automatique du header :** Docling peut produire des colonnes numériques (0, 1, 2…) quand le vrai header est dans la première ligne de données. Le script détecte ce cas et repositionne le header automatiquement.

---

# Script load_jsonline_doctags_modular.py — Injection des tables JSONL dans les DocTags

Remplace chaque balise `<otsl>…</otsl>` d'un fichier `.doctags` par un bloc `<text>` contenant le contenu JSONL de la table correspondante.

## Commandes types

```bash
uv run python pipeline_modular/simple_extraction/load_jsonline_doctags_modular.py --dotenv .env.test

uv run python pipeline_modular/simple_extraction/load_jsonline_doctags_modular.py \
  --doctags    data/output_files_preprocessing/MonDoc/MonDoc_reordered.doctags \
  --tables-dir data/output_files_preprocessing/MonDoc/tables
```

## Paramètres

| Paramètre | Alias | Défaut | Description |
|-----------|-------|--------|-------------|
| `--doctags` | `-d` | *(voir note)* | Fichier `.doctags` source produit par `reordered_doctags_modular.py`. |
| `--tables-dir` | `-t` | `<dossier parent du doctags>/tables` | Dossier contenant les `.jsonl`. |
| `--output` | `-o` | `<même dossier>/<stem>_with_tables.doctags` | Fichier `.doctags` enrichi en sortie. |
| `--dotenv` | | *(aucun)* | Fichier `.env` pour résoudre `DOC_NAME`. Ignoré si `--doctags` est fourni. |

*Note : `--doctags` absent → résout `data/output_files_preprocessing/<DOC_NAME>/<DOC_NAME>_reordered.doctags`.*

| Situation | Comportement |
|-----------|-------------|
| Aucun `.jsonl` / tous vides / aucun `<otsl>` | Passthrough — fichier copié sans modification, `exit 0` |
| Nombre de `<otsl>` ≠ nombre de tables | Warning dans les logs, remplacement jusqu'à épuisement |

---

# Script description_image_context_modular.py — Description des images via VLM

Parse les balises `<picture>` d'un `.doctags`, récupère l'image correspondante (depuis `used_images/` si pré-extraite par l'étape 01, sinon crop fitz), construit un prompt contextualisé (N éléments avant/après) et appelle le VLM pour générer une description. Remplace chaque `<picture>` par un marqueur `[[[IMAGE_DESC:N]]]` dans le doctags et exporte les descriptions dans `_image_descriptions.md`. L'injection dans le Markdown final est effectuée par l'étape 11.

**Source des images — résolution automatique :**
1. `--preextracted-images-dir` explicite
2. `used_images/` détecté automatiquement dans le dossier du `.doctags` (fichiers `pic*.png` présents — produits par `pipeline_multietape_modular.py --extract-images`)
3. Sinon : crop fitz depuis le PDF source (comportement historique)

## Variables d'environnement (.env.test)

| Variable | Obligatoire | Description |
|----------|-------------|-------------|
| `VLM_URL` | Oui (si description active) | Endpoint API VLM. |
| `VLM_MODEL_NAME` | Oui (si description active) | Nom du modèle. |
| `VLM_CA_PEM` | Non | Certificat CA custom. Fallback `certifi` si absent. |
| `DOC_NAME` | Si `--doctags`/`--pdf` absents | Résolution auto des chemins. |
| `ENABLE_IMAGE_DESCRIPTION` | Non | `true` pour activer le VLM sans passer `--image-description` (utilisé par l'orchestrateur). |

## Commandes types

```bash
# Avec descriptions VLM (images pré-extraites auto-détectées dans used_images/)
uv run python pipeline_modular/description_image/description_image_context_modular.py \
  --dotenv .env.test --image-description

# Sans descriptions (supprime les balises <picture>)
uv run python pipeline_modular/description_image/description_image_context_modular.py \
  --dotenv .env.test --no-image-description

# Dossier d'images pré-extraites explicite
uv run python pipeline_modular/description_image/description_image_context_modular.py \
  --dotenv .env.test --image-description \
  --preextracted-images-dir data/output_files_preprocessing/MonDoc/used_images

# Chemins explicites + tuning
uv run python pipeline_modular/description_image/description_image_context_modular.py \
  --doctags    data/output_files_preprocessing/MonDoc/MonDoc_reordered_with_tables.doctags \
  --pdf        data/input_files/MonDoc.pdf \
  --output     data/output_files_preprocessing/MonDoc/MonDoc_reordered_with_tables_pictures.doctags \
  --workers    4 --timeout 60 --dpi 200 --n-before 3 --n-after 3 \
  --image-description --dotenv .env.test
```

## Paramètres

| Paramètre | Alias | Défaut | Description |
|-----------|-------|--------|-------------|
| `--doctags` | `-d` | *(voir note)* | Fichier `.doctags` source. |
| `--pdf` | `-p` | *(voir note)* | PDF source (utilisé en fallback fitz si images non pré-extraites). |
| `--output` | `-o` | `<stem>_pictures.doctags` | Fichier `.doctags` enrichi. |
| `--markdown` | `-m` | `<doc>_image_descriptions.md` | Rapport Markdown des descriptions. |
| `--images-dir` | | `used_images/` | Dossier de sortie pour les PNG fitz (si pas de pré-extraction). |
| `--preextracted-images-dir` | | *(auto-détecté)* | Dossier des PNG pré-extraits par l'étape 01. Si absent, vérifie `used_images/` automatiquement. |
| `--image-description / --no-image-description` | | `False` | Active/désactive le VLM. |
| `--workers` | `-w` | `1` | Threads VLM parallèles. |
| `--timeout` | | `120` | Timeout par appel VLM (secondes). |
| `--dpi` | | `150` | Résolution DPI pour le crop fitz (fallback uniquement). |
| `--n-before` | | `5` | Éléments textuels avant l'image dans le contexte. |
| `--n-after` | | `5` | Éléments textuels après l'image dans le contexte. |
| `--language` | | `french` | Langue de la réponse VLM. |
| `--log-level` | | `INFO` | Niveau de log. |
| `--dotenv` | | *(aucun)* | Fichier `.env` pour VLM et `DOC_NAME`. |

*Note : `--doctags` absent → `data/output_files_preprocessing/<DOC_NAME>/<DOC_NAME>_reordered_with_tables.doctags`. `--pdf` absent → `data/input_files/<DOC_NAME>.pdf`.*

| Situation | Comportement |
|-----------|-------------|
| Aucune balise `<picture>` | Passthrough — doctags copié, `exit 0` |
| `--no-image-description` | Balises `<picture>` supprimées, `exit 0` |
| Images pré-extraites présentes dans `used_images/` | Chargées depuis le disque, crop fitz ignoré |
| PNG manquant dans le dossier pré-extrait | Fallback automatique sur crop fitz pour cette image |
| VLM non joignable | Arrêt immédiat, `exit 1` |
| Échec VLM sur une image | Balise `<picture>` conservée, warning, traitement continue |

---

# Script compare_image_extraction.py — Comparaison Docling vs fitz *(outil de test)*

Script autonome dans `docling_image_png/` pour comparer visuellement les deux méthodes d'extraction d'images côte à côte. Ne fait pas partie du pipeline principal.

Produit dans `data/output_files_preprocessing/<doc>/image_comparison/` :
- `docling_images/` — PNG extraits via Docling (`pil_image`, `generate_picture_images=True`)
- `fitz_images/` — PNG croppés via PyMuPDF depuis les coordonnées doctags

```bash
# DPI comparables (≈ 150 DPI dans les deux cas)
uv run python pipeline_modular/docling_image_png/compare_image_extraction.py --dotenv .env.test

# Haute résolution
uv run python pipeline_modular/docling_image_png/compare_image_extraction.py \
  --dotenv .env.test --images-scale 4.17 --dpi 300
```

| Paramètre | Défaut | Description |
|-----------|--------|-------------|
| `--images-scale` | `2.08` *(≈ 150 DPI)* | Facteur d'échelle Docling (base 72 DPI). |
| `--dpi` | `150` | DPI pour le crop fitz. |
| `--input` | *(DOC_NAME)* | PDF source. |
| `--output-dir` | `image_comparison/` | Dossier de sortie. |

---

# Script url_extaction_modular.py — Extraction des liens hypertextes

Extrait tous les liens externes (`http`, `https`, `mailto`) d'un PDF page par page via PyMuPDF. Produit un fichier JSONL — une ligne par lien trouvé.

**Peut s'exécuter en parallèle de l'étape 1** — ne dépend que du PDF source.

## Commandes types

```bash
uv run python pipeline_modular/simple_extraction/url_extaction_modular.py --dotenv .env.test

uv run python pipeline_modular/simple_extraction/url_extaction_modular.py \
  --input data/input_files/MonDoc.pdf
```

## Paramètres

| Paramètre | Alias | Défaut | Description |
|-----------|-------|--------|-------------|
| `--input` | `-i` | *(voir note)* | Chemin vers le PDF source. |
| `--output` | `-o` | `data/output_files_preprocessing/<stem>/hyperlinks_data_<stem>.jsonl` | Fichier JSONL de sortie. |
| `--dotenv` | | *(aucun)* | Fichier `.env` pour résoudre `DOC_NAME`. Ignoré si `--input` est fourni. |

*Note : `--input` absent → résout `data/input_files/<DOC_NAME>.pdf`.*

Format d'une ligne JSONL :
```json
{"page_number": 3, "text": "cliquez ici", "hyperlink": "https://example.com", "type": "URI"}
```

---

# Script url_tuning_vlm_modular.py — Intégration des liens via VLM

Reconstruit le doctags page par page en intégrant les liens hypertextes extraits par `url_extaction_modular.py`. Pour chaque page, le VLM reçoit le fragment de doctags, les liens de la page et une image PNG de la page.

**Périmètre du VLM :** insertion des URLs et corrections OCR (apostrophes, accents, espaces). La détection du formatage (gras, italique, soulignement, barré) et des couleurs est déléguée au stage 10.

## Variables d'environnement requises

| Variable | Obligatoire | Description |
|----------|-------------|-------------|
| `VLM_URL` | Oui | Endpoint API VLM. |
| `VLM_MODEL_NAME` | Oui | Nom du modèle. |
| `VLM_CA_PEM` | Non | Certificat CA custom. |
| `DOC_NAME` | Si `--input` absent | Résolution auto des chemins. |

## Commandes types

```bash
uv run python pipeline_modular/simple_extraction/url_tuning_vlm_modular.py --dotenv .env.test

uv run python pipeline_modular/simple_extraction/url_tuning_vlm_modular.py \
  --input    data/input_files/MonDoc.pdf \
  --doctags  data/output_files_preprocessing/MonDoc/MonDoc_reordered_with_tables_pictures.doctags \
  --jsonl    data/output_files_preprocessing/MonDoc/hyperlinks_data_MonDoc.jsonl \
  --output   data/output_files_preprocessing/MonDoc/MonDoc_url_vlm.doctags \
  --workers  1 --dotenv .env.test
```

## Paramètres

| Paramètre | Alias | Défaut | Description |
|-----------|-------|--------|-------------|
| `--input` | `-i` | *(voir note)* | PDF source — utilisé pour rendre les pages en image. |
| `--doctags` | `-d` | `<stem>_reordered_with_tables_pictures.doctags` | Fichier `.doctags` d'entrée. |
| `--jsonl` | `-j` | `hyperlinks_data_<stem>.jsonl` | JSONL produit par `url_extaction_modular.py`. |
| `--output` | `-o` | `<stem>_url_vlm.doctags` | Fichier `.doctags` enrichi en sortie. |
| `--workers` | `-w` | `1` | Requêtes VLM simultanées. |
| `--dotenv` | | *(aucun)* | Fichier `.env` pour la config VLM et `DOC_NAME`. |

*Note : `--input` absent → résout `data/input_files/<DOC_NAME>.pdf`.*

| Situation | Comportement |
|-----------|-------------|
| Page sans lien | VLM appelé quand même, doctags de la page inchangé |
| VLM non joignable | Arrêt immédiat, `exit 1` |
| Erreur VLM sur une page | Fallback — doctags original de la page conservé, traitement continue |

---

# Script docling_markdown_converter_modular.py — Conversion doctags → Markdown

Convertit un fichier `.doctags` enrichi en Markdown via Docling, **page par page**. Chaque page devient un bloc Markdown séparé par un marqueur `<!-- page-break -->`, que le stage 10 utilise pour découper le fichier sans re-convertir les doctags.

## Pré-traitements

| Étape | Rôle |
|-------|------|
| `_split_pages` | Découpe en blocs par page (`</page_footer>` ou `<page_break>`). Sans ce découpage, Docling s'arrête à la première page. |
| `_hoist_misplaced_tags` | Extrait les `<section_header>` et `<unordered_list>` imbriqués dans un `<ordered_list>` et les repositionne après. |

## Commandes types

```bash
uv run python pipeline_modular/simple_extraction/docling_markdown_converter_modular.py --dotenv .env.test

uv run python pipeline_modular/simple_extraction/docling_markdown_converter_modular.py \
  --input data/output_files_preprocessing/MonDoc/MonDoc_url_vlm.doctags
```

## Paramètres

| Paramètre | Alias | Défaut | Description |
|-----------|-------|--------|-------------|
| `--input` | `-i` | *(voir note)* | Fichier `.doctags` à convertir. |
| `--output` | `-o` | `<même dossier>/<stem>.md` | Fichier Markdown de sortie. |
| `--suffix` | `-s` | `_url_vlm` | Suffixe ajouté au nom du `.doctags` résolu auto. Ex. `_url_vlm` → `<DOC_NAME>_url_vlm.doctags`. Ignoré si `--input` est fourni. |
| `--dotenv` | | *(aucun)* | Fichier `.env` pour résoudre `DOC_NAME`. Ignoré si `--input` est fourni. |

*Note : `--input` absent → résout `data/output_files_preprocessing/<DOC_NAME>/<DOC_NAME><suffix>.doctags`. Par défaut (suffix `_url_vlm`) : `<DOC_NAME>_url_vlm.doctags`.*

**Sorties :**
```
data/output_files_preprocessing/MonDoc/
├── MonDoc_url_vlm.doctags    ← entrée (par défaut)
└── MonDoc_url_vlm.md         ← sortie
```

---

# Script markdown_control_vlm_modular.py — Contrôle qualité Markdown par VLM

Pour chaque page, envoie l'image PNG de la page PDF + le Markdown paginé produit par le stage 09 au VLM, qui retourne une version corrigée. Le fichier `_url_vlm.md` est découpé sur les `<!-- page-break -->` — aucune re-conversion depuis les doctags n'est effectuée.

**Périmètre du VLM :** corrections OCR, détection du formatage (gras, italique, `<u>underline</u>`, barré) et des couleurs (`<span style="color:...">`).

## Variables d'environnement requises

| Variable | Obligatoire | Description |
|----------|-------------|-------------|
| `VLM_URL` | Oui | Endpoint API VLM. |
| `VLM_MODEL_NAME` | Oui | Nom du modèle. |
| `VLM_CA_PEM` | Non | Certificat CA custom. |
| `DOC_NAME` | Si `--input` absent | Résolution auto des chemins. |

## Commandes types

```bash
uv run python pipeline_modular/simple_extraction/markdown_control_vlm_modular.py --dotenv .env.test

uv run python pipeline_modular/simple_extraction/markdown_control_vlm_modular.py \
  --input    data/input_files/MonDoc.pdf \
  --markdown data/output_files_preprocessing/MonDoc/MonDoc_url_vlm.md \
  --output   data/output_files_preprocessing/MonDoc/MonDoc_vlm_check.md \
  --workers  2 --dpi 150 --dotenv .env.test
```

## Paramètres

| Paramètre | Alias | Défaut | Description |
|-----------|-------|--------|-------------|
| `--input` | `-i` | *(voir note)* | PDF source — utilisé pour rendre les pages en image. |
| `--markdown` | `-m` | `<stem>_url_vlm.md` | Markdown paginé produit par stage 09. |
| `--output` | `-o` | `<stem>_vlm_check.md` | Markdown corrigé en sortie. |
| `--workers` | `-w` | `1` | Requêtes VLM simultanées (1–10). |
| `--dpi` | | `150` | Résolution DPI des pages PDF envoyées au VLM. |
| `--suffix` | `-s` | *(vide)* | Suffixe ajouté au nom de sortie auto. Ex. `--suffix _v2` → `_vlm_check_v2.md`. |
| `--log-level` | | `INFO` | Niveau de log. |
| `--dotenv` | | *(aucun)* | Fichier `.env` pour la config VLM et `DOC_NAME`. |

*Note : `--input` absent → résout `data/input_files/<DOC_NAME>.pdf`.*

| Situation | Comportement |
|-----------|-------------|
| VLM non joignable | Arrêt immédiat, `exit 1` |
| Nb pages PDF ≠ nb pages dans `_url_vlm.md` | Arrêt immédiat, `exit 1` — relancer stage 09 |
| Erreur HTTP 5xx / 429 sur une page | Jusqu'à 3 tentatives avec backoff, puis page exclue |
| Toutes les pages échouent | Fichier non écrit, `exit 1` |
| Pages partiellement échouées | Pages valides assemblées, `exit 0` |

---

# Script inject_image_descriptions_modular.py — Injection des descriptions (étape 11)

Remplace les marqueurs `[[[IMAGE_DESC:N]]]` laissés par l'étape 06 dans `_vlm_check.md` avec les descriptions VLM issues de `_image_descriptions.md`. S'exécute **après** le contrôle qualité VLM (étape 10), garantissant que les descriptions ne peuvent pas être supprimées par les étapes VLM précédentes.

Si `--no-image-description` a été utilisé à l'étape 06 (aucun marqueur, aucune description), le fichier est copié tel quel de `_vlm_check.md` vers `_final.md`.

## Commandes types

```bash
uv run python pipeline_modular/simple_extraction/inject_image_descriptions_modular.py --dotenv .env.test

uv run python pipeline_modular/simple_extraction/inject_image_descriptions_modular.py \
  --markdown      data/output_files_preprocessing/MonDoc/MonDoc_vlm_check.md \
  --descriptions  data/output_files_preprocessing/MonDoc/MonDoc_image_descriptions.md \
  --output        data/output_files_preprocessing/MonDoc/MonDoc_final.md
```

## Paramètres

| Paramètre | Alias | Défaut | Description |
|-----------|-------|--------|-------------|
| `--input` | `-i` | *(voir note)* | PDF source — utilisé uniquement pour résoudre le stem du document. |
| `--markdown` | `-m` | `<stem>_vlm_check.md` | Markdown produit par l'étape 10. |
| `--descriptions` | `-d` | `<stem>_image_descriptions.md` | Fichier de descriptions produit par l'étape 06. |
| `--output` | `-o` | `<stem>_final.md` | Markdown final avec descriptions injectées. |
| `--dotenv` | | *(aucun)* | Fichier `.env` pour résoudre `DOC_NAME`. |
| `--log-level` | | `INFO` | Niveau de log. |

*Note : `--input` absent → résout `DOC_NAME` depuis `--dotenv` ou l'environnement.*

| Situation | Comportement |
|-----------|-------------|
| Marqueurs présents + descriptions disponibles | Injection et écriture de `_final.md` |
| Aucun marqueur + aucune description (descriptions désactivées) | Copie `_vlm_check.md` → `_final.md`, `exit 0` |
| Descriptions disponibles mais aucun marqueur | Warning + copie telle quelle (vérifier l'étape 06) |
| Marqueurs présents mais aucune description | Warning + marqueurs conservés dans `_final.md` |

---

# Script markdown_tables_to_jsonl_modular.py — Tables Markdown → JSONL *(pipeline v3 uniquement)*

Utilisé par `fullpipeline_modular_v3.py` (étape 09) — sans équivalent dans v2, qui convertit
les tables en JSONL bien plus tôt (étape 04, `csv_to_jsonlines_modular.py`, avant toute
correction VLM). Ce script-ci lit les tables markdown natives **après** correction VLM
(`_final.md`), gère l'artefact d'en-tête dupliqué en frontière de page (correction VLM
page par page qui réémet la ligne d'en-tête sans nouveau séparateur `|---|---|`), et produit
deux sorties indépendantes à partir du même parsing :
- **Traçabilité** (toujours) : un `.jsonl` par table détectée, dans `tables_markdown/` à
  côté du markdown source — n'affecte jamais le markdown utilisé pour l'embedding.
- **Embedding** (`--embed-output`) : réécrit le document entier avec les tables remplacées
  par leurs lignes JSONL (ex. `<doc>_final_embed.md`) — `metadata_generation_modular.py`
  préfère ce fichier à `_final.md` s'il existe.

## Commandes types

```bash
uv run python pipeline_modular/simple_extraction/markdown_tables_to_jsonl_modular.py \
  --markdown data/output_files_v3/MonDoc/MonDoc_final.md

uv run python pipeline_modular/simple_extraction/markdown_tables_to_jsonl_modular.py \
  --dotenv .env.test --stage5 data/output_files_v3 \
  --embed-output data/output_files_v3/MonDoc/MonDoc_final_embed.md
```

## Paramètres

| Paramètre | Alias | Défaut | Description |
|-----------|-------|--------|-------------|
| `--doc-name` | | *(voir note)* | Nom du document sans extension. |
| `--stage5` | | `data/output_files_preprocessing/` | Racine de sortie (contient `<doc_name>/<doc_name>_final.md`). Sans effet en pratique dans `fullpipeline_modular_v3.py`, qui passe toujours `--markdown`/`--embed-output` explicitement. |
| `--markdown` | `-m` | `<stage5>/<doc_name>/<doc_name>_final.md` | Chemin explicite vers le markdown à parser. |
| `--output-dir` | `-o` | `<dossier du markdown>/tables_markdown/` | Dossier de sortie des `.jsonl` (traçabilité). |
| `--embed-output` | | *(aucun)* | Si fourni, écrit aussi le document entier (tables → JSONL) à ce chemin. |
| `--dotenv` | | *(aucun)* | Fichier `.env` pour résoudre `DOC_NAME`. |
| `--log-level` | | `INFO` | Niveau de log. |

*Note : `--doc-name` absent → résout `DOC_NAME` depuis `--dotenv` ou l'environnement.*

---

# Script metadata_generation_modular.py — Génération des métadonnées (étape 12)

Orchestre la génération complète des métadonnées pour un document : appelle `enhancement_metadata_modular.py` (résumé, intents, hyq) et `embedding_metadata_modular.py` (vecteur d'embedding), assemble le bloc de métadonnées structurées et écrit le CSV final.

**Lit depuis :** stages 1–11 dans `data/output_files_preprocessing/<doc_name>/`  
**Écrit dans :** `data/output_files_preprocessing/<doc_name>/metadata/`

## Commandes types

```bash
uv run python pipeline_modular/metadata/metadata_generation_modular.py --dotenv .env.test

uv run python pipeline_modular/metadata/metadata_generation_modular.py \
  --dotenv .env.test --doc-path "afac/Taxation/DISPENSE/Annulation d'une dispense.pdf"
```

## Paramètres

| Paramètre | Alias | Défaut | Description |
|-----------|-------|--------|-------------|
| `--dotenv` | | *(aucun)* | Fichier `.env` à charger (`DOC_NAME`, `VLM_URL`, `EMBEDDING_URL`, `VLM_CA_PEM`, …). |
| `--doc-path` | | `<DOC_NAME>.pdf` | Chemin relatif dans `folder_source` pour la hiérarchie, **premier segment = source** (ex. `afac/Taxation/MonDoc.pdf` → source `"afac"`). Si absent ou à plat (un seul segment, ou chemin absolu), source retombe sur `"afac"`. |
| `--folder-source` | | `data/input_files/` | Racine de la hiérarchie documentaire — réutilise directement l'arborescence d'entrée, pas de dossier miroir séparé. |
| `--stage1` à `--stage5` | | `data/output_files_preprocessing/` | Dossier racine des sorties par stage. Par défaut tous pointent vers `output_files_preprocessing/`. |
| `--output` | | `output_files_preprocessing/<doc>/metadata/<doc>_final.csv` | Fichier CSV de sortie. |
| `--log-level` | | `INFO` | Niveau de log. |

**Sorties :**
```
data/output_files_preprocessing/<doc_name>/metadata/
├── resume.md          ← résumé VLM
├── intent.json        ← liste d'intents
├── hyq.json           ← questions hypothétiques
├── embedding.json     ← vecteur d'embedding brut
└── <doc>_final.csv    ← CSV final (CONTENT | METADATA | EMBEDDING)
```

**CSV idempotent :** si une ligne pour ce document existe déjà dans le CSV, elle est remplacée (pas dupliquée). Safe pour les reruns.

---

# Script enhancement_metadata_modular.py — Enrichissement VLM (résumé, intents, hyq)

Génère trois enrichissements via VLM à partir du Markdown final (`_final.md`) produit par l'étape 11 : résumé court, liste d'intents (3 appels fusionnés) et questions hypothétiques. Appelé automatiquement par `metadata_generation_modular.py`, mais peut aussi s'exécuter seul.

## Commandes types

```bash
uv run python pipeline_modular/metadata/enhancement_metadata_modular.py --dotenv .env.test

uv run python pipeline_modular/metadata/enhancement_metadata_modular.py \
  --doc-name "MonDoc" --stage4 ./data/output_files_preprocessing --stage5 ./data/output_files_preprocessing
```

## Paramètres

| Paramètre | Alias | Défaut | Description |
|-----------|-------|--------|-------------|
| `--doc-name` | | *(voir note)* | Nom du document sans extension. |
| `--dotenv` | | *(aucun)* | Fichier `.env` (`VLM_URL`, `VLM_CA_PEM`, `VLM_MODEL_NAME`, `DOC_NAME`). |
| `--stage4` | | `data/output_files_preprocessing/` | Dossier racine stage 4 — lit `<stage4>/<doc>/<doc>_final.md`. |
| `--stage5` | | `data/output_files_preprocessing/` | Dossier racine stage 5 — écrit dans `<stage5>/<doc>/metadata/`. |
| `--log-level` | | `INFO` | Niveau de log. |

*Note : `--doc-name` absent → résout `DOC_NAME` depuis `--dotenv` ou l'environnement.*

---

# Script embedding_metadata_modular.py — Génération de l'embedding document

Génère le vecteur d'embedding du Markdown final (`_final.md`) via un modèle d'embedding. Appelé automatiquement par `metadata_generation_modular.py`, mais peut aussi s'exécuter seul.

## Commandes types

```bash
uv run python pipeline_modular/metadata/embedding_metadata_modular.py --dotenv .env.test

uv run python pipeline_modular/metadata/embedding_metadata_modular.py \
  --doc-name "MonDoc" --stage4 ./data/output_files_preprocessing --stage5 ./data/output_files_preprocessing
```

## Paramètres

| Paramètre | Alias | Défaut | Description |
|-----------|-------|--------|-------------|
| `--doc-name` | | *(voir note)* | Nom du document sans extension. |
| `--dotenv` | | *(aucun)* | Fichier `.env` (`EMBEDDING_URL`, `VLM_CA_PEM`, `EMBEDDING_MODEL_NAME`, `DOC_NAME`). |
| `--stage4` | | `data/output_files_preprocessing/` | Dossier racine stage 4 — lit `<stage4>/<doc>/<doc>_final.md`. |
| `--stage5` | | `data/output_files_preprocessing/` | Dossier racine stage 5 — écrit `<stage5>/<doc>/metadata/embedding.json`. |
| `--log-level` | | `INFO` | Niveau de log. |

*Note : `--doc-name` absent → résout `DOC_NAME` depuis `--dotenv` ou l'environnement.*

---

# Script hyq_embedding_doc_modular.py — Embeddings des questions hypothétiques (étape 13)

Lit `hyq.json` produit par l'étape 11, génère l'embedding de chaque question et écrit un CSV dédié par question.

## Commandes types

```bash
uv run python pipeline_modular/metadata/hyq_embedding_doc_modular.py --dotenv .env.test

uv run python pipeline_modular/metadata/hyq_embedding_doc_modular.py \
  --dotenv .env.test --doc-title "MonDoc.pdf"
```

## Paramètres

| Paramètre | Alias | Défaut | Description |
|-----------|-------|--------|-------------|
| `--dotenv` | | *(aucun)* | Fichier `.env` (`EMBEDDING_URL`, `VLM_CA_PEM`, `EMBEDDING_MODEL_NAME`, `DOC_NAME`). |
| `--doc-name` | | *(voir note)* | Nom du document sans extension. |
| `--doc-title` | | `<DOC_NAME>.pdf` | Titre avec extension — stocké dans le champ `METADATA` de chaque CSV. |
| `--stage5` | | `data/output_files_preprocessing/` | Dossier racine stage 5 — lit `<doc>/metadata/hyq.json`, écrit dans `<doc>/metadata/hyq_<doc>/`. |
| `--log-level` | | `INFO` | Niveau de log. |

*Note : `--doc-name` absent → résout `DOC_NAME` depuis `--dotenv` ou l'environnement.*

**Sorties :**
```
data/output_files_preprocessing/<doc_name>/metadata/hyq_<doc_name>/
├── question_1.csv
├── question_2.csv
└── ...
```

Chaque CSV contient une ligne (+ en-tête) :

| Colonne | Contenu |
|---------|---------|
| `CONTENT` | La question hyq |
| `METADATA` | `{"title": "<doc_name>.pdf"}` |
| `EMBEDDING` | Vecteur d'embedding sous forme CSV (`0.4, 0.8, 1.5, …`) |

**Résilience :** si l'embedding d'une question échoue, les questions suivantes sont quand même traitées. Le log final indique `N/M fichier(s) CSV écrits`.
