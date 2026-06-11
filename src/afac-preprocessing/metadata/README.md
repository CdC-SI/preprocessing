# Stage 5 — Génération et enrichissement des métadonnées

Construit les métadonnées structurées de chaque document à partir des sorties des stages 1 à 4, les enrichit via un VLM (résumé, intents, questions HYQ, embeddings), et produit un CSV final au format `CONTENT | METADATA | EMBEDDING` prêt pour l'ingestion.

---

## Place dans le pipeline global

| Stage | Sortie |
|---|---|
| Stage 1 | JSON + txt + markdown + doctags (Docling) |
| Stage 2 | Tables extraites + images décrites → doctags enrichi |
| Stage 3 | Liens hypertextes injectés → doctags final |
| Stage 4 | Markdown généré et vérifié par VLM |
| **Stage 5** | **Métadonnées enrichies + embeddings → CSV final** <- ici |

---

## Scripts en un coup d'œil

| Script | Rôle | Lance seul | Appelé par |
|---|---|---|---|
| `metadata_generation.py` | Orchestrateur principal — construit et écrit la ligne CSV finale | Oui | — |
| `enhancement_metadata.py` | Enrichissement VLM (résumé, intents, questions HYQ) | Oui | `metadata_generation.py` |
| `embedding_metadata.py` | Génère l'embedding du Markdown du document | Oui | `metadata_generation.py` |
| `hyq_embedding_doc.py` | Génère les embeddings des questions HYQ — un CSV par question | Oui | — (pipeline indépendant) |

> **Note :** contrairement aux stages précédents, ces scripts utilisent **argparse** (pas de variable d'environnement `DOC_NAME`).

---

## Script 1 — `metadata_generation.py`

Orchestre l'ensemble du pipeline de métadonnées pour un document : lecture des stages 1 à 4, appels aux scripts d'enrichissement et d'embedding, écriture de la ligne finale dans le CSV.

### Entrée

| Source | Ce qui est lu |
|---|---|
| `folder_source/<chemin_relatif>` | Hiérarchie documentaire (parent, siblings, children) |
| `stage1/<doc_name>/<doc_name>.json` | Doctype, version, nombre de pages |
| `stage2/<doc_name>/used_images/` | Images extraites (`.png`, `.jpg`, `.jpeg`) |
| `stage3/<doc_name>/hyperlinks_data_<doc_name>.jsonl` | Liens hypertextes sortants |
| `stage4/<doc_name>_vlm_check.md` | Contenu Markdown final |

### Sortie

Ajoute une ligne dans un fichier CSV (créé si absent) :

```
CONTENT                      | METADATA (JSON)                  | EMBEDDING
texte markdown du stage 4    | {"id": "...", "title": "...", …}  | "0.4, 0.8, 1.5, …"
```

Écrit également les fichiers d'enrichissement du stage 5 via les deux sous-scripts (voir ci-dessous).

### Utilisation

```bash
# Utilisation minimale — chemins de stages par défaut
python3 metadata_generation.py "Taxation/Annulation et retaxation.pdf"

# Document dans un sous-dossier
python3 metadata_generation.py "Taxation/DISPENSE/Annulation d'une dispense.pdf"

# Fichier CSV de sortie personnalisé
python3 metadata_generation.py "Adhésion/Détachement.pdf" --output ./out/documents.csv

# Chemins de stages personnalisés
python3 metadata_generation.py "Adhésion/Détachement.pdf" \
  --stage4 ./data/output_files/stage4_prod \
  --stage5 ./data/output_files/stage5_prod \
  --output ./data/output_files/metadata/documents.csv
```

### Arguments

| Argument | Obligatoire | Défaut | Description |
|---|---|---|---|
| `doc_path` | Oui | — | Chemin relatif dans `folder_source/`. Ex : `"Taxation/Annulation et retaxation.pdf"` |
| `--folder-source` | Non | `metadata/folder_source/` | Racine de la hiérarchie documentaire |
| `--stage1` | Non | `data/output_files/stage1_test` | Dossier de sortie du stage 1 |
| `--stage2` | Non | `data/output_files/stage2_test` | Dossier de sortie du stage 2 |
| `--stage3` | Non | `data/output_files/stage3_test` | Dossier de sortie du stage 3 |
| `--stage4` | Non | `data/output_files/stage4_test` | Dossier de sortie du stage 4 |
| `--stage5` | Non | `data/output_files/stage5_test` | Dossier de sortie du stage 5 |
| `--output` | Non | `data/output_files/metadata/<doc_name>_final.csv` | Chemin du CSV de sortie |

### Champs de métadonnées produits

| Champ | Type | Description |
|---|---|---|
| `id` | `str` | UUID unique du document |
| `source` | `str` | Toujours `"afac"` |
| `title` | `str` | Nom de fichier avec extension |
| `doctype` | `str` | `pdf`, `docx`, `html`, … |
| `version` | `str` | Version extraite du tableau de versioning interne |
| `visibility` | `str` | `"internal"` (par défaut) |
| `language` | `str` | `"fr"` |
| `outgoing_links` | `list[dict]` | `[{"text", "url", "page"}, …]` |
| `incoming_links` | `list[dict]` | `[{"from_doc", "text", "url"}, …]` |
| `created_at` | `str` | Date de création du PDF (ISO 8601) |
| `updated_at` | `str` | Date d'exécution du script (ISO 8601) |
| `media_type` | `list[str]` | Noms des images extraites |
| `parent_label` | `list[str]` | Dossier parent immédiat |
| `children_label` | `list[str]` | Sous-dossiers du même niveau |
| `sibling` | `list[str]` | Autres documents du même dossier |
| `content` | `str` | Nom du fichier Markdown du stage 4 |
| `page_count` | `int` | Nombre de pages |
| `page_num` | `str` | Liste des pages, ex. `"1,2,3"` |
| `chunk_count` | `int` | Nombre de chunks Markdown |
| `embedding_model` | `str` | Nom du modèle d'embedding utilisé |
| `resume` | `str` | Résumé généré par le VLM |
| `intent` | `list[str]` | Intents générés par le VLM |
| `hyq` | `list[str]` | Questions hypothétiques générées par le VLM |

---

## Script 2 — `enhancement_metadata.py`

Appelle un VLM pour générer trois champs sémantiques (résumé, intents, questions HYQ) à partir du Markdown du stage 4, puis écrit les résultats en stage 5.

### Entrée

| Source | Ce qui est lu |
|---|---|
| `stage4/<doc_name>.md` ou `stage4/<doc_name>_*.md` | Markdown final (fichier unique ou chunks concaténés) |

### Sortie

```
stage5/<doc_name>/
    resume.md     # résumé court en markdown
    intent.json   # ["intent 1", "intent 2", …]
    hyq.json      # ["Question 1 ?", "Question 2 ?", …]
```

### Utilisation

```bash
# Utilisation minimale
python3 enhancement_metadata.py "Annulation et retaxation"

# Chemins de stages personnalisés
python3 enhancement_metadata.py "Détachement" \
  --stage4 ./data/output_files/stage4_test \
  --stage5 ./data/output_files/stage5_test
```

### Arguments

| Argument | Obligatoire | Défaut | Description |
|---|---|---|---|
| `doc_name` | Oui | — | Nom du document **sans extension**. Ex : `"Annulation et retaxation"` |
| `--stage4` | Non | `data/output_files/stage4_test` | Dossier d'entrée stage 4 |
| `--stage5` | Non | `data/output_files/stage5_test` | Dossier de sortie stage 5 |

---

## Script 3 — `embedding_metadata.py`

Génère l'embedding vectoriel du Markdown du stage 4 et le persiste en stage 5.

### Entrée

| Source | Ce qui est lu |
|---|---|
| `stage4/<doc_name>.md` ou `stage4/<doc_name>_*.md` | Markdown final (fichier unique ou chunks concaténés) |

### Sortie

```
stage5/<doc_name>/
    embedding.json   # vecteur brut : [0.4, 0.8, 1.5, …]
```

Retourne également le vecteur sous forme de chaîne `"0.4, 0.8, 1.5, …"` pour la colonne `EMBEDDING` du CSV final.

### Utilisation

```bash
# Utilisation minimale
python3 embedding_metadata.py "Annulation et retaxation"

# Chemins de stages personnalisés
python3 embedding_metadata.py "Détachement" \
  --stage4 ./data/output_files/stage4_test \
  --stage5 ./data/output_files/stage5_test
```

### Arguments

| Argument | Obligatoire | Défaut | Description |
|---|---|---|---|
| `doc_name` | Oui | — | Nom du document **sans extension**. Ex : `"Annulation et retaxation"` |
| `--stage4` | Non | `data/output_files/stage4_test` | Dossier d'entrée stage 4 |
| `--stage5` | Non | `data/output_files/stage5_test` | Dossier de sortie stage 5 |

---

## Script 4 — `hyq_embedding_doc.py`

Pipeline indépendant : génère l'embedding de chaque question HYQ d'un document et écrit un CSV par question, prêt pour la recherche sémantique.

### Entrée

| Source | Ce qui est lu |
|---|---|
| `stage5/<doc_name>/hyq.json` | Liste de questions HYQ générées par `enhancement_metadata.py` |

### Sortie

```
stage5/<doc_name>/hyq_<doc_name>/
    question_1.csv
    question_2.csv
    …
    question_N.csv
```

Chaque CSV contient une ligne de données :

| Colonne | Contenu | Exemple |
|---|---|---|
| `CONTENT` | Texte de la question HYQ | `"Quelles sont les conditions d'adhésion ?"` |
| `METADATA` | Titre du document source | `{"title": "Détachement.pdf"}` |
| `EMBEDDING` | Vecteur d'embedding sous forme de chaîne | `"0.4, 0.8, 1.5, …"` |

### Utilisation

```bash
# doc_name (sans extension) + doc_title (avec extension)
python3 hyq_embedding_doc.py "Annulation et retaxation" "Annulation et retaxation.pdf"

# Chemin de stage 5 personnalisé
python3 hyq_embedding_doc.py "Détachement" "Détachement.pdf" \
  --stage5 ./data/output_files/stage5_test
```

### Arguments

| Argument | Obligatoire | Défaut | Description |
|---|---|---|---|
| `doc_name` | Oui | — | Nom du document **sans extension**. Ex : `"Annulation et retaxation"` |
| `doc_title` | Oui | — | Nom de fichier **avec extension**. Ex : `"Annulation et retaxation.pdf"` |
| `--stage5` | Non | `data/output_files/stage5_test` | Dossier stage 5 (doit contenir `<doc_name>/hyq.json`) |

---

## Enchaînement complet

```bash
# Génération complète des métadonnées pour un document (scripts 1, 2 et 3 enchaînés automatiquement)
python3 metadata_generation.py "Taxation/Annulation et retaxation.pdf"

# Génération des embeddings des questions HYQ (pipeline indépendant, après le script 1)
python3 hyq_embedding_doc.py "Annulation et retaxation" "Annulation et retaxation.pdf"
```

### Flux de transformation

```
stage4: <doc_name>_vlm_check.md
           ↓ metadata_generation.py  (appelle enhancement_metadata.py + embedding_metadata.py)
stage5: <doc_name>/resume.md
        <doc_name>/intent.json
        <doc_name>/hyq.json
        <doc_name>/embedding.json
metadata: <doc_name>_final.csv  →  CONTENT | METADATA | EMBEDDING

stage5: <doc_name>/hyq.json
           ↓ hyq_embedding_doc.py
stage5: <doc_name>/hyq_<doc_name>/question_1.csv … question_N.csv
```
