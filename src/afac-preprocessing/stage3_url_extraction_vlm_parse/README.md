# Stage 3 — Extraction et injection des liens hypertextes via VLM

Extrait tous les liens externes d'un PDF avec leurs coordonnées et leur texte d'ancrage, puis demande au VLM de les injecter au bon endroit dans le DocTags du stage 2 au format Markdown `[texte](url)`.

---

## Place dans le pipeline global

| Stage | Sortie |
|---|---|
| Stage 1 | JSON + txt + markdown + doctags (Docling) |
| Stage 2 | Tables extraites + images décrites → doctags enrichi |
| **Stage 3** | **Liens hypertextes extraits et injectés → doctags final** <- ici |
| Stage 4 | Markdown final |
| Stage 5 | Enrichissement VLM + embeddings → CSV final |

---

## Scripts en un coup d'œil

| Script | Rôle | Lit | Écrit |
|---|---|---|---|
| `get_url.py` | Extrait les liens externes du PDF (PyMuPDF) | PDF source | `hyperlinks_data_<DOC_NAME>.jsonl` |
| `url_tuning_vlm_v3.py` | Injecte les liens dans le doctags via VLM (async) | doctags stage 2 + JSONL + PDF | `<DOC_NAME>_..._url_vlm.doctags` |

> **Note :** les deux scripts lisent le nom du document via la variable d'environnement `DOC_NAME` (sans extension).

---

## Script 1 — `get_url.py`

Parcourt chaque page du PDF avec PyMuPDF, détecte les annotations de liens (`http://`, `https://`, `mailto:`), associe le texte des mots situés dans la zone du lien, et sauvegarde le tout en JSONL.

### Entrée

| Source | Ce qui est lu |
|---|---|
| `data/input_files/<DOC_NAME>.pdf` | Document PDF source |

### Sortie

```
data/output_files/stage3_test/<DOC_NAME>/
    hyperlinks_data_<DOC_NAME>.jsonl   # un lien détecté par ligne
```

Chaque ligne du JSONL a la structure suivante :

```json
{"page_number": 2, "text": "www.exemple.ch", "hyperlink": "https://www.exemple.ch", "type": "URI", "details": {"from": [120.0, 340.0, 280.0, 355.0], "uri": "https://...", ...}}
```

### Utilisation

```bash
DOC_NAME="Annulation et retaxation" python3 get_url.py

DOC_NAME="Détachement" python3 get_url.py
```

### Variables d'environnement

| Variable | Obligatoire | Description |
|---|---|---|
| `DOC_NAME` | Oui | Nom du document **sans extension**. Ex : `"Annulation et retaxation"` |

---

## Script 2 — `url_tuning_vlm_v3.py`

Traite chaque page du document en parallèle (via `asyncio`) : envoie au VLM le DocTags de la page, les liens à injecter, et une image de la page (contexte visuel). Le VLM reconstruit le DocTags de la page avec les liens intégrés au format `[texte](url)`.

### Entrée

| Source | Ce qui est lu |
|---|---|
| `data/input_files/<DOC_NAME>.pdf` | PDF source (rendu page par page à 150 DPI) |
| `data/output_files/stage2_test/<DOC_NAME>/<DOC_NAME>_reordered_with_tables_pictures.doctags` | DocTags final produit par le stage 2 |
| `data/output_files/stage3_test/<DOC_NAME>/hyperlinks_data_<DOC_NAME>.jsonl` | Liens extraits par le script 1 |

### Sortie

```
data/output_files/stage3_test/<DOC_NAME>/
    <DOC_NAME>_reordered_with_tables_pictures_url_vlm<GEN_ID>.doctags   # doctags final avec liens injectés
```

> `GEN_ID` est un suffixe optionnel permettant de versionner les sorties (ex : `_v2`, `_test`). Si vide, le fichier s'appelle `..._url_vlm.doctags`.

### Utilisation

```bash
# Utilisation standard
DOC_NAME="Annulation et retaxation" python3 url_tuning_vlm_v3.py

# Avec un suffixe de version pour la sortie
DOC_NAME="Détachement" GEN_ID="_v2" python3 url_tuning_vlm_v3.py
```

### Variables d'environnement

| Variable | Obligatoire | Défaut | Description |
|---|---|---|---|
| `DOC_NAME` | Oui | — | Nom du document **sans extension** |
| `GEN_ID` | Non | `""` | Suffixe ajouté au nom du fichier de sortie. Ex : `"_v2"` |

### Comportement page par page

Pour chaque page du document, le script :

1. Découpe le DocTags en pages (via les balises `<page_break>`, avec fallback par répartition si absent)
2. Filtre les liens du JSONL correspondant à cette page
3. Encode la page PDF en base64 (150 DPI)
4. Envoie au VLM : image + DocTags de la page + liste des liens à injecter
5. Récupère le DocTags reconstruit avec les liens intégrés au format `[texte](url)`
6. En cas d'erreur, conserve le DocTags original de la page (fallback)

> Le nombre de requêtes simultanées au VLM est limité à **1** (`MAX_WORKERS = 1`) pour éviter la surcharge.

---

## Enchaînement complet des deux scripts

```bash
export DOC_NAME="Annulation et retaxation"

# 1. Extraction des liens externes du PDF
python3 get_url.py

# 2. Injection des liens dans le doctags via VLM
python3 url_tuning_vlm_v3.py
```

### Flux de transformation du fichier DocTags

```
stage2: <DOC_NAME>_reordered_with_tables_pictures.doctags
           ↓ script 2 (url_tuning_vlm_v3.py)
stage3: <DOC_NAME>_reordered_with_tables_pictures_url_vlm.doctags   ← entrée du stage 4
```
