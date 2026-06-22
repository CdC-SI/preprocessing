# Pipeline Modular — Référence CLI

Pipeline de prétraitement PDF en 12 étapes : extraction Docling, enrichissement VLM, génération de métadonnées et embeddings.

## Vue d'ensemble des étapes

| # | Script | Rôle | Entrée | Sortie |
|---|--------|------|--------|--------|
| 01 | `pipeline_multietape_modular.py` | Extraction Docling | PDF | `.doctags` `.json` `.md` `.txt` |
| 02 | `reordered_doctags_modular.py` | Réordonnancement des blocs | `.doctags` | `_reordered.doctags` |
| 03 | `opencv_checker_modular.py` | Validation visuelle *(optionnel)* | PDF + `.doctags` | PNG par page |
| 04 | `csv_to_jsonlines_modular.py` | Conversion tables CSV → JSONL | `tables/*.csv` | `tables/*.jsonl` |
| 05 | `load_jsonline_doctags_modular.py` | Injection tables dans doctags | `_reordered.doctags` + JSONL | `_reordered_with_tables.doctags` |
| 06 | `description_image_context_modular.py` | Descriptions images via VLM *(lent)* | `_reordered_with_tables.doctags` + PDF | `_reordered_with_tables_pictures.doctags` |
| 07 | `url_extaction_modular.py` | Extraction liens hypertextes | PDF | `hyperlinks_data_<doc>.jsonl` |
| 08 | `url_tuning_vlm_modular.py` | Intégration liens via VLM + corrections OCR | `_reordered_with_tables_pictures.doctags` + JSONL | `_url_vlm.doctags` |
| 09 | `docling_markdown_converter_modular.py` | Conversion doctags → Markdown paginé | `_url_vlm.doctags` | `_url_vlm.md` *(avec `<!-- page-break -->`)* |
| 10 | `markdown_control_vlm_modular.py` | Contrôle qualité, formatage et couleurs via VLM | `_url_vlm.md` + PDF | `_vlm_check.md` |
| 11 | `metadata_generation_modular.py` | Génération métadonnées + CSV final | Sorties stages 1–4 | `metadata/<doc>_final.csv` |
| 12 | `hyq_embedding_doc_modular.py` | Embeddings questions hyq | `metadata/hyq.json` | `metadata/hyq_<doc>/question_N.csv` |

**Sortie par document :**
```
data/output_files/<doc_name>/
├── <doc>.doctags / .json / .md / .txt           ← step 01
├── <doc>_reordered.doctags                      ← step 02
├── <doc>_reordered_with_tables.doctags          ← step 05
├── <doc>_reordered_with_tables_pictures.doctags ← step 06
├── <doc>_url_vlm.doctags                        ← step 08
├── <doc>_url_vlm.md                             ← step 09
├── <doc>_vlm_check.md                           ← step 10
├── hyperlinks_data_<doc>.jsonl                  ← step 07
├── <doc>_image_descriptions.md                  ← step 06
├── opencv_validation/                           ← step 03
├── tables/                                      ← steps 01, 04, 05
├── used_images/                                 ← step 06
└── metadata/                                    ← steps 11, 12
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

Lance les 12 étapes en séquence. Chaque étape reçoit le `--dotenv` résolu pour que `DOC_NAME` soit cohérent sur toute la durée du run.

## Commandes types

```bash
# Pipeline complet
uv run python pipeline_modular/automate_pipeline_example/fullpipeline_modular_v2.py --dotenv .env.test

# Reprendre après un échec à l'étape 8
uv run python pipeline_modular/automate_pipeline_example/fullpipeline_modular_v2.py --dotenv .env.test --from-step 8

# Seulement les métadonnées (étapes 11–12)
uv run python pipeline_modular/automate_pipeline_example/fullpipeline_modular_v2.py --dotenv .env.test --from-step 11

# Extraction seulement, sans métadonnées
uv run python pipeline_modular/automate_pipeline_example/fullpipeline_modular_v2.py --dotenv .env.test --to-step 10

# Ignorer opencv (étape 3) et descriptions images (étape 6, lente)
uv run python pipeline_modular/automate_pipeline_example/fullpipeline_modular_v2.py --dotenv .env.test --skip-steps 3,6
```

## Paramètres

| Paramètre | Défaut | Description |
|-----------|--------|-------------|
| `--dotenv` | `.env.test` | Fichier `.env` transmis à chaque étape. |
| `--from-step` | `1` | Première étape à exécuter (1–12, inclus). |
| `--to-step` | `12` | Dernière étape à exécuter (1–12, inclus). |
| `--skip-steps` | *(aucun)* | Étapes à ignorer, séparées par virgule. Ex. `--skip-steps 3,6`. |

**Comportement en cas d'échec :** si une étape retourne un code ≠ 0, le pipeline s'arrête immédiatement et affiche `[FAILED] <script> exited with code N`. Les étapes suivantes ne sont pas exécutées.

**Note sur `--skip-steps` :** ignorer une étape ne supprime pas la dépendance sur ses fichiers de sortie. Si l'étape 1 est ignorée mais que `<doc>.doctags` n'existe pas, l'étape 2 échouera avec un message d'erreur explicite (fichier introuvable).

---

# Script pipeline_multietape_modular.py — Extraction Docling

Script unifié qui remplace `pipeline_multietape.py` (stage 1) et `export_table_docling.py` (stage 2). Une seule conversion Docling produit tous les formats demandés.

## Commande complète

```bash
uv run python pipeline_modular/simple_extraction/pipeline_multietape_modular.py \
  --input     data/input_files/MonDoc.pdf \
  --output-dir ./data/output_files/MonDoc \
  --formats   json md txt doctags \
  --lang      fr en \
  --threads   4 \
  --device    cuda \
  --no-tables \
  --dotenv    .env.test
```

## Workflow .env.test
```bash
uv run python pipeline_modular/simple_extraction/pipeline_multietape_modular.py --dotenv .env.test
```

## Paramètres

| Paramètre | Alias | Défaut | Description |
|-----------|-------|--------|-------------|
| `--input` | `-i` | *(voir note)* | Chemin vers le PDF à traiter. |
| `--output-dir` | `-o` | `data/output_files/<nom_doc>/` | Dossier de sortie. Créé automatiquement. |
| `--formats` | `-f` | tous | Formats parmi `json md txt doctags`. |
| `--lang` | `-l` | `fr` | Code(s) de langue EasyOCR. Ex. `fr en ar`. |
| `--threads` | `-t` | `4` | Threads CPU alloués à Docling. |
| `--device` | | `cuda` | Accélérateur : `cuda` ou `cpu`. |
| `--no-ocr` | | désactivé | Désactive EasyOCR. Utile pour les PDFs natifs. |
| `--no-tables` | | désactivé | Désactive la détection de tableaux. |
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

---

# Script reordered_doctags_modular.py — Réordonnancement des blocs DocTags

Corrige l'ordre des blocs extraits par Docling dans un fichier `.doctags`. Trie par position verticale (y0) puis horizontale (x0), page par page.

## Commandes types

```bash
# Résolution auto depuis DOC_NAME
uv run python pipeline_modular/simple_extraction/reordered_doctags_modular.py --dotenv .env.test

# Chemin explicite
uv run python pipeline_modular/simple_extraction/reordered_doctags_modular.py \
  --input data/output_files/MonDoc/MonDoc.doctags
```

## Paramètres

| Paramètre | Alias | Défaut | Description |
|-----------|-------|--------|-------------|
| `--input` | `-i` | *(voir note)* | Fichier `.doctags` source. |
| `--output` | `-o` | `<même dossier>/<stem>_reordered.doctags` | Fichier de sortie. |
| `--dotenv` | | *(aucun)* | Fichier `.env` pour résoudre `DOC_NAME`. Ignoré si `--input` est fourni. |

*Note : `--input` absent → résout `data/output_files/<DOC_NAME>/<DOC_NAME>.doctags`.*

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
  --doctags  data/output_files/MonDoc/MonDoc.doctags \
  --output-dir data/output_files/MonDoc/opencv_validation

# DPI réduit pour validation rapide
uv run python pipeline_modular/simple_extraction/opencv_checker_modular.py \
  --input data/input_files/MonDoc.pdf --dpi 150
```

## Paramètres

| Paramètre | Alias | Défaut | Description |
|-----------|-------|--------|-------------|
| `--input` | `-i` | *(voir note)* | Chemin vers le PDF source. |
| `--doctags` | `-d` | `data/output_files/<stem>/<stem>.doctags` | Fichier `.doctags`. |
| `--output-dir` | `-o` | `data/output_files/<stem>/opencv_validation/` | Dossier de sortie PNG. |
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
  --input-dir data/output_files/MonDoc/tables
```

## Paramètres

| Paramètre | Alias | Défaut | Description |
|-----------|-------|--------|-------------|
| `--input-dir` | `-i` | *(voir note)* | Dossier contenant les `*.csv` à convertir. |
| `--output-dir` | `-o` | même dossier que `--input-dir` | Dossier de sortie pour les `.jsonl`. |
| `--dotenv` | | *(aucun)* | Fichier `.env` pour résoudre `DOC_NAME`. Ignoré si `--input-dir` est fourni. |

*Note : `--input-dir` absent → résout `data/output_files/<DOC_NAME>/tables/`.*

**Détection automatique du header :** Docling peut produire des colonnes numériques (0, 1, 2…) quand le vrai header est dans la première ligne de données. Le script détecte ce cas et repositionne le header automatiquement.

---

# Script load_jsonline_doctags_modular.py — Injection des tables JSONL dans les DocTags

Remplace chaque balise `<otsl>…</otsl>` d'un fichier `.doctags` par un bloc `<text>` contenant le contenu JSONL de la table correspondante.

## Commandes types

```bash
uv run python pipeline_modular/simple_extraction/load_jsonline_doctags_modular.py --dotenv .env.test

uv run python pipeline_modular/simple_extraction/load_jsonline_doctags_modular.py \
  --doctags    data/output_files/MonDoc/MonDoc_reordered.doctags \
  --tables-dir data/output_files/MonDoc/tables
```

## Paramètres

| Paramètre | Alias | Défaut | Description |
|-----------|-------|--------|-------------|
| `--doctags` | `-d` | *(voir note)* | Fichier `.doctags` source produit par `reordered_doctags_modular.py`. |
| `--tables-dir` | `-t` | `<dossier parent du doctags>/tables` | Dossier contenant les `.jsonl`. |
| `--output` | `-o` | `<même dossier>/<stem>_with_tables.doctags` | Fichier `.doctags` enrichi en sortie. |
| `--dotenv` | | *(aucun)* | Fichier `.env` pour résoudre `DOC_NAME`. Ignoré si `--doctags` est fourni. |

*Note : `--doctags` absent → résout `data/output_files/<DOC_NAME>/<DOC_NAME>_reordered.doctags`.*

| Situation | Comportement |
|-----------|-------------|
| Aucun `.jsonl` / tous vides / aucun `<otsl>` | Passthrough — fichier copié sans modification, `exit 0` |
| Nombre de `<otsl>` ≠ nombre de tables | Warning dans les logs, remplacement jusqu'à épuisement |

---

# Script description_image_context_modular.py — Description des images via VLM

Parse les balises `<picture>` d'un `.doctags`, crop les zones correspondantes depuis le PDF, construit un prompt contextualisé (N éléments avant/après) et appelle le VLM pour générer une description. Remplace chaque `<picture>` par la description produite.

## Variables d'environnement requises

| Variable | Obligatoire | Description |
|----------|-------------|-------------|
| `VLM_URL` | Oui (si `--image-description`) | Endpoint API VLM. |
| `VLM_MODEL_NAME` | Oui (si `--image-description`) | Nom du modèle. |
| `VLM_CA_PEM` | Non | Certificat CA custom. Fallback `certifi` si absent. |
| `DOC_NAME` | Si `--doctags`/`--pdf` absents | Résolution auto des chemins. |

## Commandes types

```bash
# Avec descriptions VLM
uv run python pipeline_modular/description_image/description_image_context_modular.py \
  --dotenv .env.test --image-description

# Sans descriptions (supprime les balises <picture>)
uv run python pipeline_modular/description_image/description_image_context_modular.py \
  --dotenv .env.test --no-image-description

# Chemins explicites + tuning
uv run python pipeline_modular/description_image/description_image_context_modular.py \
  --doctags    data/output_files/MonDoc/MonDoc_reordered_with_tables.doctags \
  --pdf        data/input_files/MonDoc.pdf \
  --output     data/output_files/MonDoc/MonDoc_reordered_with_tables_pictures.doctags \
  --workers    4 --timeout 60 --dpi 200 --n-before 3 --n-after 3 \
  --image-description --dotenv .env.test
```

## Paramètres

| Paramètre | Alias | Défaut | Description |
|-----------|-------|--------|-------------|
| `--doctags` | `-d` | *(voir note)* | Fichier `.doctags` source. |
| `--pdf` | `-p` | *(voir note)* | PDF source pour le crop des images. |
| `--output` | `-o` | `<stem>_pictures.doctags` | Fichier `.doctags` enrichi. |
| `--markdown` | `-m` | `<doc>_image_descriptions.md` | Rapport Markdown des descriptions. |
| `--images-dir` | | `used_images/` | Dossier de sortie pour les PNG cropés. |
| `--image-description / --no-image-description` | | `False` | Active/désactive le VLM. |
| `--workers` | `-w` | `1` | Threads VLM parallèles. |
| `--timeout` | | `120` | Timeout par appel VLM (secondes). |
| `--dpi` | | `150` | Résolution DPI pour le crop. |
| `--n-before` | | `5` | Éléments textuels avant l'image dans le contexte. |
| `--n-after` | | `5` | Éléments textuels après l'image dans le contexte. |
| `--language` | | `french` | Langue de la réponse VLM. |
| `--log-level` | | `INFO` | Niveau de log. |
| `--dotenv` | | *(aucun)* | Fichier `.env` pour VLM et `DOC_NAME`. |

*Note : `--doctags` absent → `data/output_files/<DOC_NAME>/<DOC_NAME>_reordered_with_tables.doctags`. `--pdf` absent → `data/input_files/<DOC_NAME>.pdf`.*

| Situation | Comportement |
|-----------|-------------|
| Aucune balise `<picture>` | Passthrough — doctags copié, `exit 0` |
| `--no-image-description` | Balises `<picture>` supprimées, PNG exportés, `exit 0` |
| VLM non joignable | Arrêt immédiat, `exit 1` |
| Échec VLM sur une image | Balise `<picture>` conservée, warning, traitement continue |

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
| `--output` | `-o` | `data/output_files/<stem>/hyperlinks_data_<stem>.jsonl` | Fichier JSONL de sortie. |
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
  --doctags  data/output_files/MonDoc/MonDoc_reordered_with_tables_pictures.doctags \
  --jsonl    data/output_files/MonDoc/hyperlinks_data_MonDoc.jsonl \
  --output   data/output_files/MonDoc/MonDoc_url_vlm.doctags \
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
  --input data/output_files/MonDoc/MonDoc_url_vlm.doctags
```

## Paramètres

| Paramètre | Alias | Défaut | Description |
|-----------|-------|--------|-------------|
| `--input` | `-i` | *(voir note)* | Fichier `.doctags` à convertir. |
| `--output` | `-o` | `<même dossier>/<stem>.md` | Fichier Markdown de sortie. |
| `--suffix` | `-s` | `_url_vlm` | Suffixe ajouté au nom du `.doctags` résolu auto. Ex. `_url_vlm` → `<DOC_NAME>_url_vlm.doctags`. Ignoré si `--input` est fourni. |
| `--dotenv` | | *(aucun)* | Fichier `.env` pour résoudre `DOC_NAME`. Ignoré si `--input` est fourni. |

*Note : `--input` absent → résout `data/output_files/<DOC_NAME>/<DOC_NAME><suffix>.doctags`. Par défaut (suffix `_url_vlm`) : `<DOC_NAME>_url_vlm.doctags`.*

**Sorties :**
```
data/output_files/MonDoc/
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
  --markdown data/output_files/MonDoc/MonDoc_url_vlm.md \
  --output   data/output_files/MonDoc/MonDoc_vlm_check.md \
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

# Script metadata_generation_modular.py — Génération des métadonnées (étape 11)

Orchestre la génération complète des métadonnées pour un document : appelle `enhancement_metadata_modular.py` (résumé, intents, hyq) et `embedding_metadata_modular.py` (vecteur d'embedding), assemble le bloc de métadonnées structurées et écrit le CSV final.

**Lit depuis :** stages 1–4 dans `data/output_files/<doc_name>/`  
**Écrit dans :** `data/output_files/<doc_name>/metadata/`

## Commandes types

```bash
uv run python pipeline_modular/metadata/metadata_generation_modular.py --dotenv .env.test

uv run python pipeline_modular/metadata/metadata_generation_modular.py \
  --dotenv .env.test --doc-path "Taxation/DISPENSE/Annulation d'une dispense.pdf"
```

## Paramètres

| Paramètre | Alias | Défaut | Description |
|-----------|-------|--------|-------------|
| `--dotenv` | | *(aucun)* | Fichier `.env` à charger (`DOC_NAME`, `VLM_URL`, `EMBEDDING_URL`, `VLM_CA_PEM`, …). |
| `--doc-path` | | `<DOC_NAME>.pdf` | Chemin relatif dans `folder_source` pour la hiérarchie (ex. `Taxation/MonDoc.pdf`). Si absent, structure plate. |
| `--folder-source` | | `metadata/folder_source/` | Racine de la hiérarchie documentaire. |
| `--stage1` à `--stage5` | | `data/output_files/` | Dossier racine des sorties par stage. Par défaut tous pointent vers `output_files/`. |
| `--output` | | `output_files/<doc>/metadata/<doc>_final.csv` | Fichier CSV de sortie. |
| `--log-level` | | `INFO` | Niveau de log. |

**Sorties :**
```
data/output_files/<doc_name>/metadata/
├── resume.md          ← résumé VLM
├── intent.json        ← liste d'intents
├── hyq.json           ← questions hypothétiques
├── embedding.json     ← vecteur d'embedding brut
└── <doc>_final.csv    ← CSV final (CONTENT | METADATA | EMBEDDING)
```

**CSV idempotent :** si une ligne pour ce document existe déjà dans le CSV, elle est remplacée (pas dupliquée). Safe pour les reruns.

---

# Script enhancement_metadata_modular.py — Enrichissement VLM (résumé, intents, hyq)

Génère trois enrichissements via VLM à partir du Markdown stage 4 : résumé court, liste d'intents (3 appels fusionnés) et questions hypothétiques. Appelé automatiquement par `metadata_generation_modular.py`, mais peut aussi s'exécuter seul.

## Commandes types

```bash
uv run python pipeline_modular/metadata/enhancement_metadata_modular.py --dotenv .env.test

uv run python pipeline_modular/metadata/enhancement_metadata_modular.py \
  --doc-name "MonDoc" --stage4 ./data/output_files --stage5 ./data/output_files
```

## Paramètres

| Paramètre | Alias | Défaut | Description |
|-----------|-------|--------|-------------|
| `--doc-name` | | *(voir note)* | Nom du document sans extension. |
| `--dotenv` | | *(aucun)* | Fichier `.env` (`VLM_URL`, `VLM_CA_PEM`, `VLM_MODEL_NAME`, `DOC_NAME`). |
| `--stage4` | | `data/output_files/` | Dossier racine stage 4 — lit `<stage4>/<doc>/<doc>_vlm_check.md`. |
| `--stage5` | | `data/output_files/` | Dossier racine stage 5 — écrit dans `<stage5>/<doc>/metadata/`. |
| `--log-level` | | `INFO` | Niveau de log. |

*Note : `--doc-name` absent → résout `DOC_NAME` depuis `--dotenv` ou l'environnement.*

---

# Script embedding_metadata_modular.py — Génération de l'embedding document

Génère le vecteur d'embedding du Markdown stage 4 via un modèle d'embedding. Appelé automatiquement par `metadata_generation_modular.py`, mais peut aussi s'exécuter seul.

## Commandes types

```bash
uv run python pipeline_modular/metadata/embedding_metadata_modular.py --dotenv .env.test

uv run python pipeline_modular/metadata/embedding_metadata_modular.py \
  --doc-name "MonDoc" --stage4 ./data/output_files --stage5 ./data/output_files
```

## Paramètres

| Paramètre | Alias | Défaut | Description |
|-----------|-------|--------|-------------|
| `--doc-name` | | *(voir note)* | Nom du document sans extension. |
| `--dotenv` | | *(aucun)* | Fichier `.env` (`EMBEDDING_URL`, `VLM_CA_PEM`, `EMBEDDING_MODEL_NAME`, `DOC_NAME`). |
| `--stage4` | | `data/output_files/` | Dossier racine stage 4 — lit `<stage4>/<doc>/<doc>_vlm_check.md`. |
| `--stage5` | | `data/output_files/` | Dossier racine stage 5 — écrit `<stage5>/<doc>/metadata/embedding.json`. |
| `--log-level` | | `INFO` | Niveau de log. |

*Note : `--doc-name` absent → résout `DOC_NAME` depuis `--dotenv` ou l'environnement.*

---

# Script hyq_embedding_doc_modular.py — Embeddings des questions hypothétiques (étape 12)

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
| `--stage5` | | `data/output_files/` | Dossier racine stage 5 — lit `<doc>/metadata/hyq.json`, écrit dans `<doc>/metadata/hyq_<doc>/`. |
| `--log-level` | | `INFO` | Niveau de log. |

*Note : `--doc-name` absent → résout `DOC_NAME` depuis `--dotenv` ou l'environnement.*

**Sorties :**
```
data/output_files/<doc_name>/metadata/hyq_<doc_name>/
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
