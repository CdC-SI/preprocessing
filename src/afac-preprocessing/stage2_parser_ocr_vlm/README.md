# Stage 2 — Pipeline de parsing, extraction de tables et description d'images VLM

Extrait les tables d'un document PDF en CSV/JSONL, les injecte dans les DocTags du stage 1, puis décrit chaque image via un VLM pour produire un fichier DocTags entièrement enrichi (tables + descriptions d'images).

---

## Place dans le pipeline global

| Stage | Sortie |
|---|---|
| Stage 1 | JSON + txt + markdown + doctags (Docling) |
| **Stage 2** | **Tables extraites + images décrites → doctags enrichi** <- ici |
| Stage 3 | Hyperliens |
| Stage 4 | Markdown final |
| Stage 5 | Enrichissement VLM + embeddings → CSV final |

---

## Scripts en un coup d'œil

| Script | Rôle | Lit | Écrit |
|---|---|---|---|
| `export_table_docling.py` | Extrait les tables du PDF via Docling | PDF source | CSV + HTML par table |
| `csv_to_json.py` | Convertit les CSV de tables en JSONL | CSV des tables (stage 2) | JSONL par table |
| `load_jsonline_docling.py` | Injecte les tables JSONL dans le doctags (remplace `<otsl>`) | doctags stage 1 + JSONL | `_reordered_with_tables.doctags` |
| `description_image_context.py` | Décrit les images via VLM et remplace les `<picture>` | doctags + PDF source | doctags final + markdown + PNG |

> **Note :** tous les scripts lisent le nom du document via la variable d'environnement `DOC_NAME` (sans extension).

---

## Script 1 — `export_table_docling.py`

Convertit le PDF avec Docling et exporte chaque table détectée au format CSV et HTML.

### Entrée

| Source | Ce qui est lu |
|---|---|
| `data/input_files/<DOC_NAME>.pdf` | Document PDF source |

### Sortie

```
data/output_files/stage2_test/<DOC_NAME>/tables/
    <DOC_NAME>-table-1.csv
    <DOC_NAME>-table-1.html
    <DOC_NAME>-table-2.csv
    <DOC_NAME>-table-2.html
    ...
```

### Utilisation

```bash
DOC_NAME="Annulation et retaxation" python3 export_table_docling.py

DOC_NAME="Détachement" python3 export_table_docling.py
```

### Variables d'environnement

| Variable | Obligatoire | Description |
|---|---|---|
| `DOC_NAME` | Oui | Nom du document **sans extension**. Ex : `"Annulation et retaxation"` |

---

## Script 2 — `csv_to_json.py`

Convertit chaque fichier CSV de table en JSONL, avec détection automatique des en-têtes et déduplication des colonnes.

### Entrée

| Source | Ce qui est lu |
|---|---|
| `data/output_files/stage2_test/<DOC_NAME>/tables/*.csv` | Fichiers CSV produits par le script 1 |

### Sortie

Un fichier `.jsonl` est créé à côté de chaque `.csv` :

```
data/output_files/stage2_test/<DOC_NAME>/tables/
    <DOC_NAME>-table-1.jsonl    # {"col1": "val", "col2": "val", ...}
    <DOC_NAME>-table-2.jsonl
    ...
```

Chaque ligne du JSONL correspond à une ligne de la table. Les valeurs `NaN` sont converties en `null`, les nombres en chaînes de caractères.

### Utilisation

```bash
DOC_NAME="Annulation et retaxation" python3 csv_to_json.py

DOC_NAME="Détachement" python3 csv_to_json.py
```

### Variables d'environnement

| Variable | Obligatoire | Description |
|---|---|---|
| `DOC_NAME` | Oui | Nom du document **sans extension**, identique au script 1 |

---

## Script 3 — `load_jsonline_docling.py`

Remplace chaque balise `<otsl>...</otsl>` dans le doctags réordonné (stage 1) par le contenu JSONL de la table correspondante, enveloppé dans une balise `<text>`.

### Entrée

| Source | Ce qui est lu |
|---|---|
| `data/output_files/stage1_test/<DOC_NAME>/<DOC_NAME>_reordered.doctags` | DocTags réordonné produit par le stage 1 |
| `data/output_files/stage2_test/<DOC_NAME>/tables/*.jsonl` | Fichiers JSONL produits par le script 2 |

### Sortie

```
data/output_files/stage2_test/<DOC_NAME>/
    <DOC_NAME>_reordered_with_tables.doctags   # doctags avec <otsl> remplacés par <text>JSONL</text>
```

> Si aucun fichier JSONL n'est trouvé, le doctags d'entrée est copié sans modification pour ne pas bloquer la suite du pipeline.

### Utilisation

```bash
DOC_NAME="Annulation et retaxation" python3 load_jsonline_docling.py

DOC_NAME="Détachement" python3 load_jsonline_docling.py
```

### Variables d'environnement

| Variable | Obligatoire | Description |
|---|---|---|
| `DOC_NAME` | Oui | Nom du document **sans extension** |

---

## Script 4 — `description_image_context.py`

Détecte les balises `<picture>` dans le doctags, extrait les images correspondantes du PDF, les envoie au VLM avec leur contexte textuel (5 éléments avant/après), puis remplace chaque balise par la description générée.

### Entrée

| Source | Ce qui est lu |
|---|---|
| `data/input_files/<DOC_NAME>.pdf` | PDF source (crop des images) |
| `data/output_files/stage2_test/<DOC_NAME>/<DOC_NAME>_reordered_with_tables.doctags` | DocTags produit par le script 3 |

### Sortie

```
data/output_files/stage2_test/<DOC_NAME>/
    <DOC_NAME>_reordered_with_tables_pictures.doctags   # doctags final (tables + images décrites)
    <DOC_NAME>_image_descriptions.md                    # rapport Markdown des descriptions générées
    used_images/
        <DOC_NAME>_page1_x<x0>_y<y0>_x<x1>_y<y1>.png  # PNG de chaque image extraite
        ...
```

### Utilisation

```bash
# Avec descriptions VLM activées (comportement par défaut)
DOC_NAME="Annulation et retaxation" python3 description_image_context.py

# Désactiver les descriptions VLM (les balises <picture> sont supprimées sans être décrites)
DOC_NAME="Détachement" ENABLE_IMAGE_DESCRIPTION=false python3 description_image_context.py
```

### Variables d'environnement

| Variable | Obligatoire | Défaut | Description |
|---|---|---|---|
| `DOC_NAME` | Oui | — | Nom du document **sans extension** |
| `ENABLE_IMAGE_DESCRIPTION` | Non | `true` | Mettre à `false` pour désactiver les appels VLM (les `<picture>` sont alors supprimées) |

### Contenu du rapport Markdown généré

Chaque image produit une section dans `<DOC_NAME>_image_descriptions.md` :

```markdown
## OK - Image 1/3 — Page 2 | `loc(120, 80, 380, 240)`

[description générée par le VLM]

## WARNING - Image 2/3 — Page 3 | `loc(50, 300, 450, 480)`

> Aucune description générée.
```

---

## Enchaînement complet des quatre scripts

```bash
export DOC_NAME="Annulation et retaxation"

# 1. Extraction des tables (PDF → CSV + HTML)
python3 export_table_docling.py

# 2. Conversion des tables (CSV → JSONL)
python3 csv_to_json.py

# 3. Injection des tables dans le doctags (<otsl> → <text>JSONL</text>)
python3 load_jsonline_docling.py

# 4. Description des images via VLM (<picture> → description textuelle)
python3 description_image_context.py
```

### Flux de transformation du fichier DocTags

```
stage1: <DOC_NAME>_reordered.doctags
           ↓ script 3
stage2: <DOC_NAME>_reordered_with_tables.doctags
           ↓ script 4
stage2: <DOC_NAME>_reordered_with_tables_pictures.doctags   ← entrée du stage 3
```
