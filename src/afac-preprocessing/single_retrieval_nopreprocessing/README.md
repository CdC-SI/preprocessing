# single_retrieval_nopreprocessing — Baseline docling brut vs pipeline de prétraitement

Mesure l'apport (ou le coût) du pipeline de prétraitement en comparant deux représentations
d'un même corpus de documents, évaluées avec **les mêmes questions HyQ** et **les mêmes
métriques** (Recall/Precision/nDCG/MRR@k) :
- **baseline** : markdown produit directement par Docling (`<doc>.md`), sans aucun enrichissement.
- **pipeline** : markdown enrichi par le pipeline de prétraitement (v2, v3, ou v3 sans
  descriptions d'images — cf. `pipeline_modular/automate_pipeline_example/`).

Seule la représentation du document change ; les questions HyQ (texte + embedding) sont
toujours réutilisées telles quelles depuis `metadata/hyq_<doc>/question_N.csv` — jamais
régénérées, pour garantir une comparaison à questions identiques d'un bout à l'autre.

---

## Scripts

| Script | Rôle |
|---|---|
| `single_docling_baseline.py` | Génère `baseline_metadata.csv` (CONTENT/METADATA/HYQ/EMBEDDING par doc, embeddé sur le markdown docling brut) + `baseline_results.csv` (métriques par question HyQ, réutilise `evaluate_doc()` de `retrieval_protocol_evaluation/`). |
| `compare_baseline_report.py` | Fusionne `baseline_results.csv` et `data/evaluation_results/global_summary.csv` (produit par `evaluate_all_docs.py`), calcule le delta par métrique@k, génère un rapport markdown + graphiques. Verdict VLM optionnel en tête du rapport. |
| `single_doc_preview_report.py` | Aperçu rapide **un seul document**, avant de relancer tout le corpus : compare baseline/v2/v3(-embed/-noembed) en longueur de contenu et en self-similarité (embedding du document vs. ses propres questions HyQ). **Ne calcule pas** de Recall@k/nDCG@k réels — nécessite plusieurs documents pour classer, cf. docstring du fichier. |

## Workflow type (corpus complet)

Prérequis : le pipeline de prétraitement (v2 et/ou v3) a déjà tourné sur le corpus cible
(cf. `pipeline_modular/automate_pipeline_example/batch_pipeline_all_pdfs.py`), donc chaque
document a un `<doc>.md` (brut), un `metadata/hyq.json` + `metadata/hyq_<doc>/question_N.csv`
(questions HyQ déjà embeddées).

```bash
# 1. Baseline (docling brut) — CSV + résultats
uv run python single_retrieval_nopreprocessing/single_docling_baseline.py --dotenv .env.test

# 2. Évaluation du pipeline avec les mêmes métriques (sortie : data/evaluation_results/global_summary.csv)
uv run python retrieval_protocol_evaluation/evaluate_all_docs.py

# 3. Rapport de comparaison (sortie : data/baseline_evaluation/comparison_report.md)
uv run python single_retrieval_nopreprocessing/compare_baseline_report.py --dotenv .env.test
```

Pour comparer une variante v3 (ou v3-noimg) plutôt que v2 :
```bash
uv run python retrieval_protocol_evaluation/evaluate_all_docs.py \
  --stage5 data/output_files_v3 --output-dir data/evaluation_results_v3

uv run python single_retrieval_nopreprocessing/compare_baseline_report.py --dotenv .env.test \
  --pipeline-summary data/evaluation_results_v3/global_summary.csv \
  --output data/baseline_evaluation/comparison_report_v3.md
```
`compare_baseline_report.py` ne lit qu'un seul `global_summary.csv` à la fois — un `--output`
dédié par variante évite d'écraser le rapport v2. `single_docling_baseline.py` (étape 1)
n'a besoin d'être relancé qu'une fois : la baseline (markdown docling brut) est indépendante
de la variante du pipeline testée.

## Sorties

- `data/baseline_evaluation/baseline_metadata.csv`, `baseline_results.csv`
- `data/baseline_evaluation/comparison_report.md` + `data/baseline_evaluation/charts/*.png`
- `data/output_files_baseline/<doc>/<doc>.md` — copie du markdown brut réellement utilisé pour l'embedding baseline, pour inspection visuelle côte à côte avec `data/output_files_preprocessing/<doc>/` et `data/output_files_v3/<doc>/`.
- `data/baseline_evaluation/single_doc_preview_<doc>.md` (aperçu un-seul-document, `single_doc_preview_report.py`)

## Lecture du rapport de comparaison

`delta = baseline − pipeline` : **positif** ⇒ la baseline fait mieux (le prétraitement
dégrade le retrieval) ; **négatif** ⇒ le prétraitement améliore. Sur le corpus Adhésion (20
docs), la baseline a systématiquement surpassé le pipeline v2 sur toutes les métriques — piste
identifiée : les descriptions d'images VLM (quasi identiques d'un document à l'autre, ex. le
logo institutionnel en en-tête) et la conversion des tables en JSON-lines (v2) diluent le
signal sémantique propre à chaque document dans l'embedding, d'autant plus sur les documents
courts. La variante v3-noimg (`fullpipeline_modular_v3.py --no-image-description --skip-steps 9`
— descriptions désactivées, tables gardées en markdown natif même pour l'embedding) vise à
isoler cet effet.