# Stage 4 — Conversion DocTags → Markdown et contrôle qualité VLM

Convertit le DocTags enrichi du stage 3 en Markdown via Docling, puis envoie chaque page au VLM (avec son image PDF) pour corriger et valider le contenu avant l'ingestion finale.

---

## Place dans le pipeline global

| Stage | Sortie |
|---|---|
| Stage 1 | JSON + txt + markdown + doctags (Docling) |
| Stage 2 | Tables extraites + images décrites → doctags enrichi |
| Stage 3 | Liens hypertextes injectés → doctags final |
| **Stage 4** | **Markdown généré + vérifié par VLM** <- ici |
| Stage 5 | Enrichissement VLM + embeddings → CSV final |

---

## Scripts en un coup d'œil

| Script | Rôle | Lit | Écrit |
|---|---|---|---|
| `convert_doctags_to_markdown.py` | Convertit le doctags en Markdown via Docling | doctags stage 3 | `<DOC_NAME>.md` |
| `markdown_control_vlm.py` | Vérifie et corrige le Markdown page par page via VLM | Markdown + PDF source | `<DOC_NAME>_vlm_check.md` |

> **Note :** les deux scripts lisent le nom du document via `DOC_NAME` et un suffixe optionnel via `GEN_ID`.

---

## Script 1 — `convert_doctags_to_markdown.py`

Parse le DocTags enrichi avec Docling et exporte le document en Markdown. Applique deux prétraitements sur le DocTags avant conversion, puis deux post-traitements sur le Markdown généré.

### Entrée

| Source | Ce qui est lu |
|---|---|
| `data/output_files/stage3_test/<DOC_NAME>/<DOC_NAME>_reordered_with_tables_pictures_url_vlm.doctags` | DocTags final produit par le stage 3 |

### Sortie

```
data/output_files/stage4_test/
    <DOC_NAME><GEN_ID>.md   # Markdown généré par Docling
```

### Prétraitements sur le DocTags (avant conversion Docling)

| Étape | Problème traité | Comportement |
|---|---|---|
| `_split_pages` | Docling s'arrête après la 1re page si le fichier est un seul bloc `<doctag>` | Découpe le contenu en un bloc `<doctag>…</doctag>` par page via `</page_footer>` |
| `_hoist_misplaced_tags` | Docling écrase les `<section_header_level_N>` et `<unordered_list>` imbriqués dans un `<ordered_list>` | Les extrait de la liste et les replace juste après le `</ordered_list>` correspondant |

### Post-traitements sur le Markdown généré

| Balise source | Rendu final |
|---|---|
| `[[COLOR:red]]texte[[/COLOR]]` | `<span style="color:red">texte</span>` |
| `\_\_texte\_\_` | `<u>texte</u>` |

### Utilisation

```bash
# Conversion standard
DOC_NAME="Annulation et retaxation" python3 convert_doctags_to_markdown.py

# Avec suffixe de version sur la sortie
DOC_NAME="Détachement" GEN_ID="_v2" python3 convert_doctags_to_markdown.py
```

### Variables d'environnement

| Variable | Obligatoire | Défaut | Description |
|---|---|---|---|
| `DOC_NAME` | Oui | — | Nom du document **sans extension**. Ex : `"Annulation et retaxation"` |
| `GEN_ID` | Non | `""` | Suffixe ajouté au nom du fichier de sortie. Ex : `"_v2"` |

---

## Script 2 — `markdown_control_vlm.py`

Vérifie et corrige le Markdown généré par le script 1, page par page : pour chaque page, le VLM reçoit l'image de la page (PDF) et le Markdown complet du document, puis retourne la correction pour cette page uniquement. Les corrections sont ensuite assemblées en un seul fichier final.

### Entrée

| Source | Ce qui est lu |
|---|---|
| `data/input_files/<DOC_NAME>.pdf` | PDF source (rendu page par page à 150 DPI) |
| `data/output_files/stage4_test/<DOC_NAME><GEN_ID>.md` | Markdown généré par le script 1 |

### Sortie

```
data/output_files/stage4_test/
    <DOC_NAME><GEN_ID>_vlm_check.md   # Markdown corrigé et vérifié par le VLM ← entrée du stage 5
```

### Utilisation

```bash
# Contrôle qualité standard
DOC_NAME="Annulation et retaxation" python3 markdown_control_vlm.py

# Avec suffixe de version (doit correspondre au GEN_ID du script 1)
DOC_NAME="Détachement" GEN_ID="_v2" python3 markdown_control_vlm.py
```

### Variables d'environnement

| Variable | Obligatoire | Défaut | Description |
|---|---|---|---|
| `DOC_NAME` | Oui | — | Nom du document **sans extension** |
| `GEN_ID` | Non | `""` | Suffixe de version — doit correspondre à celui utilisé dans le script 1 |

### Comportement page par page

Pour chaque page du document, le script :

1. Rend la page du PDF en image PNG (150 DPI) et l'encode en base64
2. Construit le prompt avec le Markdown complet du document + le numéro de page courante
3. Envoie au VLM : image + prompt → reçoit le Markdown corrigé pour cette page uniquement
4. En cas d'erreur, la page est ignorée (chaîne vide) et le traitement continue
5. Assemble toutes les corrections dans l'ordre des pages en un seul fichier final

> Le nombre de requêtes simultanées est limité à **1** (`MAX_WORKERS = 1`). La température est fixée à `0.0` pour des corrections déterministes.

---

## Enchaînement complet des deux scripts

```bash
export DOC_NAME="Annulation et retaxation"

# 1. Conversion DocTags → Markdown (Docling)
python3 convert_doctags_to_markdown.py

# 2. Contrôle qualité et correction du Markdown (VLM)
python3 markdown_control_vlm.py
```

### Flux de transformation

```
stage3: <DOC_NAME>_reordered_with_tables_pictures_url_vlm.doctags
           ↓ script 1 (convert_doctags_to_markdown.py)
stage4: <DOC_NAME><GEN_ID>.md
           ↓ script 2 (markdown_control_vlm.py)
stage4: <DOC_NAME><GEN_ID>_vlm_check.md   ← entrée du stage 5 (metadata)
```
