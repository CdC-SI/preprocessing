# pipeline_baseline — Baseline docling brut vs pipeline de prétraitement

Mesure l'apport (ou le coût) du pipeline de prétraitement en comparant deux représentations
d'un même corpus de documents, évaluées avec **les mêmes questions HyQ** et **les mêmes
métriques** (Recall/Precision/nDCG/MRR@k) :
- **baseline** : markdown produit directement par Docling (`<doc>.md`), sans aucun enrichissement.
- **pipeline** : markdown enrichi par le pipeline de prétraitement (CLI `afac-preprocess run`).

Seule la représentation du document change ; les questions HyQ (texte + embedding) sont
toujours réutilisées telles quelles depuis `metadata/hyq_<doc>/question_N.csv` — jamais
régénérées, pour garantir une comparaison à questions identiques d'un bout à l'autre.

---

## Scripts

| Script | Rôle |
|---|---|
| `single_docling_baseline.py` | Génère `baseline_metadata.csv` (CONTENT/METADATA/HYQ/EMBEDDING par doc, embeddé sur le markdown docling brut) + `baseline_results.csv` (métriques par question HyQ, réutilise `evaluate_doc()` de `retrieval_protocol_evaluation/`). |
| `compare_baseline_report.py` | Fusionne `baseline_results.csv` et `data/pipeline_evaluation/global_summary.csv` (produit par `evaluate_all_docs.py`), calcule le delta par métrique@k, génère un rapport markdown + graphiques. Verdict VLM optionnel en tête du rapport. |
| `single_doc_preview_report.py` | Aperçu rapide **un seul document**, avant de relancer tout le corpus : compare baseline/pipeline en longueur de contenu et en self-similarité (embedding du document vs. ses propres questions HyQ). **Ne calcule pas** de Recall@k/nDCG@k réels — nécessite plusieurs documents pour classer, cf. docstring du fichier. |

> **Le reranker (`retrieval_protocol_evaluation/reranker.py`) n'est jamais comparé sur la baseline** — `single_docling_baseline.py` appelle toujours `evaluate_doc(..., use_reranker=False)`, et même activé ça ne fonctionnerait pas : le reranker lit `resume.md`, qui n'existe que côté pipeline (généré par `enhancement_metadata.py`), jamais pour le markdown docling brut. La comparaison baseline vs pipeline repose donc uniquement sur la colonne "semantic" des deux côtés.

## Workflow type (corpus complet)

Prérequis : le pipeline de prétraitement a déjà tourné sur le corpus cible
(`uv run afac-preprocess run --input <dossier>`), donc chaque
document a un `<doc>.md` (brut), un `metadata/hyq.json` + `metadata/hyq_<doc>/question_N.csv`
(questions HyQ déjà embeddées).

```bash
# 1. Baseline (docling brut) — CSV + résultats
uv run python -m afac_preprocessing.pipeline_baseline.single_docling_baseline --dotenv .env.test

# 2. Évaluation du pipeline avec les mêmes métriques (sortie : data/pipeline_evaluation/global_summary.csv)
uv run python -m afac_preprocessing.retrieval_protocol_evaluation.evaluate_all_docs

# 3. Rapport de comparaison (sortie : data/baseline_evaluation/comparison_report.md)
uv run python -m afac_preprocessing.pipeline_baseline.compare_baseline_report --dotenv .env.test
```

## Sorties

- `data/baseline_evaluation/baseline_metadata.csv`, `baseline_results.csv`
- `data/baseline_evaluation/comparison_report.md` + `data/baseline_evaluation/charts/*.png`
- `data/output_files_baseline/<doc>/<doc>.md` — copie du markdown brut réellement utilisé pour l'embedding baseline, pour inspection visuelle côte à côte avec `data/output_files_preprocessing/<doc>/`.
- `data/baseline_evaluation/single_doc_preview_<doc>.md` (aperçu un-seul-document, `single_doc_preview_report.py`)

## Lecture du rapport de comparaison

`delta = baseline − pipeline` : **positif** ⇒ la baseline fait mieux (le prétraitement
dégrade le retrieval) ; **négatif** ⇒ le prétraitement améliore. Sur le corpus Adhésion (20
docs), la baseline a systématiquement surpassé le pipeline sur toutes les métriques — piste
identifiée : les descriptions d'images VLM (quasi identiques d'un document à l'autre, ex. le
logo institutionnel en en-tête) et la conversion des tables en JSON-lines diluent le
signal sémantique propre à chaque document dans l'embedding, d'autant plus sur les documents
courts.