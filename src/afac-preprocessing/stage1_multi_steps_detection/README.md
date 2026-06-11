# Introduciton :
# Stage 1 — Pipeline de détection multi-étapes

Cette première étape du pipeline de preprocessing à pour abjectif d'extraire à l'aide de docling les information sur un document passé comme point d'entré, en quatre formats structurés (JSON, Markdown, texte brut, DocTags) via Docling + EasyOCR, puis de corriger l'ordre des balises
Ici, 3 scripts sont appelé successivement que sont :
1) `pipeline_multietape.py`, pour une première extraction des informations contenues dans un document.
2) `opencv_checker.py`, pour générer les encarts autour des éléments détectés par docling pour assurer un premier contrôle sur la qualité de la détection.
3) `control_doctags_balise_loc_yo_v2.py`, pour réorganiser les balises precedemments générées avec le pipeline docling EasyOCR qui peuvent ne pas être placées dans le bonne ordre d'apparirtion.

Pour effectuer l'ensemble de ces étapes le support docling à été utilisée :
https://docling-project.github.io/docling/_generated/examples/custom_convert/


## Place dans le pipeline global
Le pipeline global développé pour l'AF est composé de 5 étapes. 
Ce document ne traite qu'exclusivement de l'étape 1

| Stage | Sortie |
|---|---|
| **Stage 1** | **JSON + txt + markdown + doctags (Docling)** <- ici|
| Stage 2 | Images extraites |
| Stage 3 | Hyperliens |
| Stage 4 | Markdown final |
| Stage 5 | Enrichissement VLM + embeddings → CSV final |


## Scripts en un coup d'œil

| Script | Rôle | Lance seul | Appelé par |
|---|---|---|---|
| `pipeline_multietape.py` | Conversion Docling — génère JSON, MD, TXT, DocTags | Oui | — |
| `control_doctags_balise_loc_y0_v2.py` | Réordonne les balises DocTags par position verticale (y0) - et x0 si égalité | Oui | — |
| `opencv_checker.py` | Génère des images annotées pour le contrôle qualité visuel | Oui | — |

> **Note :** les trois scripts lisent le nom du document via la variable d'environnement `DOC_NAME` (sans extension), c'est à dire comme suit "Mon_document" (pas de .docx, .pdf, ...)


## Script 1 — `pipeline_multietape.py`

Convertit un PDF en quatre formats exportables via Docling (OCR EasyOCR français, détection de tableaux, accélération CUDA).

### Entrée

| Source | Ce qui est lu |
|---|---|
| `data/input_files/<DOC_NAME>.pdf` | Document PDF à traiter |

### Sortie

```
data/output_files/stage1_test/<DOC_NAME>/
    <DOC_NAME>.json       # structure complète
    <DOC_NAME>.md         # export Markdown
    <DOC_NAME>.txt        # export texte brut
    <DOC_NAME>.doctags    # balises structurées avec coordonnées
```

### Utilisation

Pour utilise ce script seul, il faut dans un premier temps renseigner le champ DOC_NAME="" dans votre environnement, example (.env, .env.test, ...)
puis, vous pouvez utiliser la commande :

```bash
# Example d'utilisation du script après avec placé FOC_NAME="My_document" dans votre environnement
python3 pipeline_multietape.py
```

### Variables d'environnement

| Variable | Obligatoire | Description |
|---|---|---|
| `DOC_NAME` | Oui | Nom du document **sans extension**. Ex : `"Mon_document"` |


## Script 2 — `control_doctags_balise_loc_y0_v2.py`

Réordonne les blocs d'un fichier `.doctags` page par page selon les coordonnées `y0` (puis `x0`) pour garantir que l'ordre de lecture correspond à l'ordre visuel du PDF.

### Entrée

| Source | Ce qui est lu |
|---|---|
| `data/output_files/stage1_test/<DOC_NAME>/<DOC_NAME>.doctags` | Fichier DocTags brut produit par le script 1 "My_Document.doctags"|

Pour ce script il faut également s'assurer que dnas DOC_NAME

### Sortie

```
data/output_files/stage1_test/<DOC_NAME>/
    <DOC_NAME>_reordered.doctags   # balises réordonnées par position verticale
```

### Logique de tri

| Cas | Comportement |
|---|---|
| Bloc sans coordonnées (`y0 = None`) | Placé en tête de page, ordre d'origine conservé |
| `<ordered_list>` | Traité comme un bloc unique (non décomposé) |
| `<unordered_list>` | Décomposé en `<list_item>` individuels, triés séparément |
| Blocs avec coordonnées | Triés par `y0` croissant, puis `x0` croissant |

### Utilisation

```bash
# Réordonnancement des doctags après le script 1
DOC_NAME="Annulation et retaxation" python3 control_doctags_balise_loc_y0_v2.py
```

### Variables d'environnement

| Variable | Obligatoire | Description |
|---|---|---|
| `DOC_NAME` | Oui | Nom du document **sans extension**, identique au script 1 |

## Script 3 — `opencv_checker.py`

Génère une image annotée par page à partir du PDF et de ses `.doctags` : chaque zone détectée est entourée d'un rectangle coloré selon son type, pour un contrôle qualité visuel rapide.

### Entrée

| Source | Ce qui est lu |
|---|---|
| `data/input_files/<DOC_NAME>.pdf` | PDF source (pour le rendu des pages) |
| `data/output_files/stage1_test/<DOC_NAME>/<DOC_NAME>.doctags` | Fichier DocTags brut (coordonnées des zones) |

### Sortie

```
data/output_files/stage1_test/opencv_doctags_allpages_<DOC_NAME>/
    page_1_doctags_boxes.png
    page_2_doctags_boxes.png
    ...
    page_N_doctags_boxes.png
```

### Code couleur des zones annotées

| Type de balise | Couleur |
|---|---|
| `text` | Vert |
| `table` | Bleu |
| `picture` / `figure` | Rouge / Orange vif |
| `section_header_level_1` | Orange |
| `page_header` | Cyan |
| `page_footer` | Magenta |
| `caption` | Rose |
| `footnote` | Gris |
| Balise inconnue | Jaune (couleur par défaut) |

> Les coordonnées DocTags sont normalisées sur une grille 500×500 (convention Docling) et converties en pixels à 300 DPI pour correspondre aux images générées.

### Utilisation

```bash
# Génération des images de contrôle
DOC_NAME="Annulation et retaxation" python3 opencv_checker.py

DOC_NAME="Détachement" python3 opencv_checker.py
```

### Variables d'environnement

| Variable | Obligatoire | Description |
|---|---|---|
| `DOC_NAME` | Oui | Nom du document **sans extension**, identique aux scripts précédents |

---

## Enchaînement complet des trois scripts

```bash
export DOC_NAME="Annulation et retaxation"

# 1. Conversion Docling
python3 pipeline_multietape.py

# 2. Correction de l'ordre des balises
python3 control_doctags_balise_loc_y0_v2.py

# 3. Contrôle qualité visuel
python3 opencv_checker.py
```
