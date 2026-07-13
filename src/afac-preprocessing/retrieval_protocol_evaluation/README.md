# Retrieval Protocol Evaluation — AFAC

Évaluation du retrieval sémantique sur les documents AFAC : pour chaque question
hypothétique (HyQ) générée par le pipeline, mesure si son document source est retrouvé
dans le top-k d'une recherche par similarité cosinus (embedding question vs embedding
document). Métriques : Recall/Precision/nDCG/MRR@k, pour k ∈ `1, 3, 5, 10, 20`.

## Architecture

```
config.py            # Constantes : chemins, TOP_KS, suffixes des dossiers
loaders.py            # Chargement des CSVs → objets Python + numpy arrays
similarity.py         # Matrice de similarité cosinus + ranking
metrics.py             # Recall/Precision/nDCG/MRR@k — fonctions pures
reranker.py            # Wrapper API reranker (pipeline avec reranking)
report.py               # Export CSV des résultats + graphiques matplotlib
evaluate.py              # CLI — un document à la fois (debug/inspection)
evaluate_all_docs.py     # CLI batch — tout le corpus, semantic + reranker
```

Source des données par document : `<doc>/metadata/<doc>_final.csv` (embedding du contenu)
et `<doc>/metadata/hyq_<doc>/question_N.csv` (embedding de chaque question HyQ) — mêmes
modèle d'embedding des deux côtés, donc comparables directement par similarité cosinus.

Aucun venv séparé — dépendances déclarées dans le `pyproject.toml` racine (`uv sync`).

## Usage

```bash
# Un document, toutes ses questions (debug/inspection)
uv run python retrieval_protocol_evaluation/evaluate.py --doc-name "Adhésion traitement"

# Tout le corpus — semantic + reranker, résumé global
uv run python retrieval_protocol_evaluation/evaluate_all_docs.py
```

`--help` sur chaque script pour la liste des paramètres (`--stage5`, `--output-dir`,
`--top-ks`, ...).

`evaluate_all_docs.py` découvre tous les documents sous `--stage5` (un `<doc>_final.csv` +
au moins une question HyQ), calcule les métriques avec les deux pipelines (sémantique seul,
puis sémantique + reranker), et écrit :
- `<doc>/evaluation_results.csv` + `_reranked.csv` — détail par document, `<métrique>_at_k.png`
- `global_summary.csv` — moyennes par doc (`sem_mean_*`, `rer_mean_*`) — consommé par
  `pipeline_baseline/compare_baseline_report.py` pour comparer au docling brut
- `global_*_comparison.png` — graphiques agrégés

## Points d'attention / TODO

- [ ] Valider la qualité des questions HyQ générées (revue avec Kieran)
- [ ] Confirmer la méthode de validation du dataset "Golden"
- [ ] Comparer d'autres variantes : chunking, metadata enrichi, modèles d'embedding différents
      (cf. `pipeline_baseline/` pour la comparaison baseline vs pipeline déjà en place)
