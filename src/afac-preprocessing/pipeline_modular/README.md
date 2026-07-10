# Pipeline Modular — Référence

Pipeline de prétraitement PDF en 13 étapes : extraction Docling, enrichissement VLM,
génération de métadonnées et embeddings. Chaque étape est un script autonome exécutable
seul (`--help` pour la liste complète de ses paramètres) ou via l'orchestrateur
[`pipeline_extraction.py`](automate_pipeline_example/README.md).

## Les 13 étapes

| # | Nom (`--from-step`/`--only`) | Script | Rôle |
|---|---|---|---|
| 01 | `docling-extract` | `simple_extraction/docling_extract.py` | Extraction Docling (doctags/json/md/txt) + export images PNG *(optionnel)* |
| 02 | `reorder-doctags` | `simple_extraction/reordered_doctags.py` | Réordonnancement des blocs par position (y0, x0) |
| 03 | `opencv-check` | `simple_extraction/opencv_checker.py` | Validation visuelle des bounding boxes *(optionnel, skip par défaut — cf. plus bas)* |
| 04 | `csv-to-jsonlines` | `simple_extraction/csv_to_jsonlines.py` | Conversion des tables CSV → JSONL |
| 05 | `load-jsonline-doctags` | `simple_extraction/load_jsonline_doctags.py` | Injection des tables JSONL dans le doctags |
| 06 | `image-description` | `description_image/description_image_context.py` | Descriptions d'images via VLM *(lent)* — émet des marqueurs `[[[IMAGE_DESC:N]]]` |
| 07 | `url-extraction` | `simple_extraction/url_extaction.py` | Extraction des liens hypertextes du PDF |
| 08 | `url-tuning` | `simple_extraction/url_tuning_vlm.py` | Intégration des liens + corrections OCR via VLM |
| 09 | `markdown-convert` | `simple_extraction/docling_markdown_converter.py` | Conversion doctags → Markdown paginé |
| 10 | `markdown-control` | `simple_extraction/markdown_control_vlm.py` | Contrôle qualité, formatage et couleurs via VLM |
| 11 | `inject-image-descriptions` | `simple_extraction/inject_image_descriptions.py` | Injection des descriptions d'images → `_final.md` |
| 12 | `metadata-generation` | `metadata/metadata_generation.py` | Génération métadonnées + CSV final |
| 13 | `hyq-embedding` | `metadata/hyq_embedding_doc.py` | Embeddings des questions hypothétiques |

**Sortie par document** (`data/output_files_preprocessing/<doc_name>/`) :
```
<doc>.doctags/.json/.md/.txt   ← 01        hyperlinks_data_<doc>.jsonl  ← 07
<doc>_reordered.doctags        ← 02        <doc>_url_vlm.doctags        ← 08
tables/                        ← 01,04,05  <doc>_url_vlm.md             ← 09
used_images/                   ← 01 ou 06  <doc>_vlm_check.md           ← 10
<doc>_image_descriptions.md    ← 06        <doc>_final.md                ← 11
opencv_validation/             ← 03        metadata/                     ← 12,13
                                              ├── resume.md, intent.json, hyq.json, embedding.json
                                              ├── <doc>_final.csv
                                              └── hyq_<doc>/question_N.csv
```

## Lancer le pipeline

Voir [automate_pipeline_example/README.md](automate_pipeline_example/README.md) pour
`pipeline_extraction.py` (orchestrateur — un doc ou un dossier entier) et les autres
runners. Résumé rapide :

```bash
uv run python pipeline_modular/automate_pipeline_example/pipeline_extraction.py --dotenv .env.test
uv run python pipeline_modular/automate_pipeline_example/pipeline_extraction.py --dotenv .env.test --from-step markdown-convert
uv run python pipeline_modular/automate_pipeline_example/pipeline_extraction.py --dotenv .env.test --skip-steps opencv-check,image-description
uv run python pipeline_modular/automate_pipeline_example/pipeline_extraction.py --list-steps
```

`--opencv-check` est désactivée par défaut (QA visuelle uniquement, ne produit rien en aval)
— l'activer avec `--with-opencv-check`.

## Lancer une étape seule

Chaque script résout `DOC_NAME`/les chemins d'entrée-sortie automatiquement depuis
`--dotenv`, ou accepte des chemins explicites. Exemple :

```bash
uv run python pipeline_modular/simple_extraction/docling_extract.py --dotenv .env.test --extract-images
uv run python pipeline_modular/metadata/metadata_generation.py --dotenv .env.test
```

`--help` sur n'importe quel script liste ses paramètres et leurs défauts.

## Comportements notables (non évidents depuis `--help`)

| Étape | Comportement |
|---|---|
| `opencv-check` | Outil de validation uniquement — sortie non consommée par les étapes suivantes. |
| `csv-to-jsonlines` | Détecte et corrige automatiquement un header Docling mal positionné (colonnes numériques). |
| `load-jsonline-doctags` | Passthrough (`exit 0`) si aucune table `<otsl>` dans le doctags. |
| `image-description` | Résolution auto des images : dossier pré-extrait (`used_images/`) → sinon crop fitz depuis le PDF. Passthrough si aucune balise `<picture>` ou si désactivée (`ENABLE_IMAGE_DESCRIPTION=false` / `--no-image-description`) — ne crée alors aucun fichier de sortie. |
| `url-extraction` | Peut s'exécuter en parallèle de `docling-extract` — ne dépend que du PDF source. |
| `url-tuning`, `markdown-control` | VLM injoignable → `exit 1` immédiat. Échec sur une page/image isolée → contenu original conservé, warning, le reste continue. |
| `markdown-control` | Nombre de pages PDF ≠ nombre de pages dans le markdown d'entrée → `exit 1` (relancer `markdown-convert`). |
| `inject-image-descriptions` | Si `image-description` n'a produit aucun fichier (voir ci-dessus), copie le markdown tel quel vers `_final.md`. |
| `metadata-generation` | Écriture CSV idempotente — une ligne existante pour le même document est remplacée, pas dupliquée. |
| `hyq-embedding` | Résilient par question : un échec d'embedding n'interrompt pas les questions suivantes. |

## Scripts hors pipeline principal

- `metadata/enhancement_metadata.py`, `metadata/embedding_metadata.py` — appelés automatiquement par `metadata-generation`, exécutables seuls pour debug.
- `simple_extraction/markdown_tables_to_jsonl.py` — utilisé par le profil `fullpipeline_modular_v3.py` (local, non suivi par git), sans équivalent dans ce pipeline.
- `docling_image_png/compare_image_extraction.py` — outil de comparaison visuelle Docling vs fitz, ne fait pas partie du pipeline.
