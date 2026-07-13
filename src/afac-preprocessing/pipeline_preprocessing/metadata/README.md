# metadata — Génération des métadonnées (étapes 12-13)

Collecte, enrichit et stocke les métadonnées structurées de chaque document. S'exécute
après les étapes 01-11 et produit une ligne CSV par document : **CONTENT** | **METADATA** | **EMBEDDING**.

Scripts : `metadata_generation.py` (orchestrateur, étape `metadata-generation`) appelle
`enhancement_metadata.py` (resume/intent/hyq via VLM) et `embedding_metadata.py` (embedding
du contenu). `hyq_embedding_doc.py` (étape `hyq-embedding`) embedde ensuite chaque question
hypothétique séparément.

## D'où viennent les champs METADATA

| Champ | Source |
|---|---|
| `uuid` | UUID5 déterministe dérivé du chemin relatif — stable d'un run à l'autre |
| `source`, `parent_label`, `children_label`, `sibling` | Position du document dans `folder_source` (`--input-dir`) |
| `title`, `doctype` | Nom de fichier + mimetype du JSON Docling (étape 01) |
| `version` | Première table à colonne "Version" du JSON Docling, dernière valeur non vide |
| `page_count`, `page_num` | Nombre de pages du JSON Docling |
| `created_at` | Métadonnées internes du PDF (PyMuPDF) |
| `updated_at` | Horodatage UTC de l'exécution |
| `outgoing_links` / `incoming_links` | Hyperliens extraits à l'étape `url-extraction` (ce document / documents qui le référencent) |
| `media_type` | Images extraites (`used_images/`, étape 01 ou 06) |
| `content`, `chunk_count` | Nom du markdown final (`_final_embed.md` si présent, sinon `_final.md`) |
| `resume`, `intent`, `hyq` | Générés par VLM depuis le markdown final (`enhancement_metadata.py`) |
| `embedding_model` | Nom du modèle résolu depuis la config VLM |

## Embedding

Le markdown final (`_final_embed.md` si présent — tables en JSONL, sinon `_final.md`) est
envoyé au modèle d'embedding. Le vecteur est écrit dans `metadata/embedding.json` et dans
la colonne EMBEDDING du CSV.

## Sortie

```
data/output_files_preprocessing/<doc_name>/metadata/
├── resume.md / intent.json / hyq.json / embedding.json
├── <doc_name>_final.csv          ← CONTENT | METADATA | EMBEDDING (idempotent — une ligne existante est remplacée)
└── hyq_<doc_name>/question_N.csv ← un CSV par question hypothétique embeddée
```

`--help` sur chaque script pour la liste des paramètres (`--input-dir`, `--image-dir`,
`--url-dir`, `--markdown-dir`, `--output-dir`, `--skip-enhancement`, ...).
