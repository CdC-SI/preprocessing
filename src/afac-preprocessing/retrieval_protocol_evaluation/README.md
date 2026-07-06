# Retrieval Protocol Evaluation — AFAC

Évaluation de la qualité du retrieval sémantique sur les documents AFAC, en utilisant les questions hypothétiques (HyQ) générées par le pipeline.

---

## I. Objectif

Mesurer si le doc source associé à une question HyQ est retrouvé dans le top-K des résultats d'une recherche par similarité cosinus.

Les métriques calculées :
- **Recall@k** — le doc source est-il dans les k premiers résultats ?
- **Precision@k**, **nDCG@k**, **MRR@k** — implémentées (cf. `metrics.py` et `evaluate_all_docs.py`)

Avec plusieurs valeurs de k : `1, 3, 5, 10, 20`.

---

## II. Principe

```
Question HyQ (embedding) ──────────────────────────────────┐
                                                            ▼
                            cosine_similarity(q_emb, doc_embs)
                                                            │
                                                   Tri décroissant
                                                            │
                                              Top-K documents candidats
                                                            │
                                     Le doc source est dans le top-K ?
                                                   │           │
                                                  OUI          NON
                                             recall@k = 1  recall@k = 0
```

### Source des données

Le pipeline AFAC génère pour chaque document :

| Fichier | Contenu |
|---|---|
| `<doc>/metadata/<doc>_final.csv` | `CONTENT \| METADATA \| EMBEDDING` du document |
| `<doc>/metadata/hyq_<doc>/question_N.csv` | `CONTENT \| METADATA \| EMBEDDING` de la question HyQ N |

L'embedding des questions et des documents est produit par le même modèle, ce qui rend la comparaison par similarité cosinus directement applicable.

---

## III. Architecture modulaire

```
retrieval_protocol_evaluation/
├── config.py            # Constantes : chemins, TOP_KS, suffixes des dossiers
├── loaders.py           # Chargement des CSVs → objets Python + numpy arrays
├── similarity.py        # Matrice de similarité cosinus + ranking
├── metrics.py           # Recall/Precision/nDCG/MRR@k — fonctions pures
├── reranker.py          # Wrapper API reranker (pipeline avec reranking)
├── report.py            # Export CSV des résultats + graphiques matplotlib
├── evaluate.py          # Entrypoint CLI (argparse) — un document à la fois
└── evaluate_all_docs.py # Entrypoint CLI batch — tout le corpus, semantic + reranker, cf. §VII
```

### Flux de données

```
loaders.py
  load_hyq_questions()       →  list[QuestionRecord]  (content, source_title, embedding)
  load_all_doc_embeddings()  →  list[DocRecord]        (doc_name, embedding)
          │
          ▼
similarity.py
  compute_similarity_matrix()  →  np.ndarray (n_questions × n_docs)
  rank_docs()                  →  indices triés par score décroissant
          │
          ▼
metrics.py
  evaluate_question()  →  {k: recall_at_k}  pour chaque valeur de k
          │
          ▼
report.py
  save_results_csv()    →  results/recall_at_k_results.csv
  plot_recall_curves()  →  results/recall_at_k.png
```

---

## IV. Installation

Les dépendances sont gérées dans le `pyproject.toml` racine du projet (`afac-preprocessing`). Pas de venv séparé à gérer.

```bash
# Depuis la racine du projet afac-preprocessing
uv sync
```

Dépendances utilisées par ce module : `scikit-learn`, `numpy`, `pandas`, `matplotlib` — toutes déclarées dans `pyproject.toml`.

---

## V. Usage CLI

### Test rapide — 1 question, 1 document

```bash
uv run python retrieval_protocol_evaluation/evaluate.py --doc-name "Adhésion traitement" --question-idx 1
```

### Toutes les questions d'un document

```bash
uv run python retrieval_protocol_evaluation/evaluate.py --doc-name "Adhésion traitement"
```

### Options complètes

```bash
uv run python retrieval_protocol_evaluation/evaluate.py \
  --doc-name "Adhésion traitement" \
  --stage5 data/output_files_preprocessing \
  --output-dir data/evaluation_results \
  --top-ks 1,3,5,10,20 \
  --log-level DEBUG
```

| Argument | Défaut | Description |
|---|---|---|
| `--doc-name` | *(requis)* | Nom du document sans extension |
| `--stage5` | `data/output_files_preprocessing` | Dossier racine, un sous-dossier par document (`<stage5>/<doc_name>/metadata/<doc>_final.csv`) |
| `--output-dir` | `data/evaluation_results` | Dossier de sortie des résultats |
| `--top-ks` | `1,3,5,10,20` | Valeurs de k séparées par des virgules |
| `--question-idx` | *(toutes)* | Index 1-based pour tester une seule question |
| `--log-level` | `INFO` | Niveau de log : DEBUG / INFO / WARNING / ERROR |

---

## VI. Résultats — exemple live

**Document testé** : `Adhésion traitement` | **4 docs dans le corpus** | **12 questions HyQ**

```
Q01 | recall@1=1 recall@5=1 | top1='Adhésion traitement'       (score: 0.7674)
Q02 | recall@1=1 recall@5=1 | top1='Adhésion traitement'       (score: 0.5655)
Q03 | recall@1=1 recall@5=1 | top1='Adhésion traitement'       (score: 0.6672)
Q04 | recall@1=1 recall@5=1 | top1='Adhésion traitement'       (score: 0.6928)
Q05 | recall@1=0 recall@5=1 | top1='Demande prématurée'        (score: 0.7093)
Q06 | recall@1=0 recall@5=1 | top1='Demande prématurée'        (score: 0.7008)
Q07 | recall@1=0 recall@5=1 | top1='Confirmer l'adhésion'     (score: 0.5664)
Q08 | recall@1=0 recall@5=1 | top1='Demande de justificatifs'  (score: 0.7439)
Q09 | recall@1=0 recall@5=1 | top1='Confirmer l'adhésion'     (score: 0.5729)
Q10 | recall@1=0 recall@5=1 | top1='Demande prématurée'        (score: 0.4846)
Q11 | recall@1=0 recall@5=1 | top1='Demande prématurée'        (score: 0.6305)
Q12 | recall@1=0 recall@5=1 | top1='Confirmer l'adhésion'     (score: 0.4161)
```

**Résumé des métriques (corpus de 4 docs) :**

| k | Recall@k moyen |
|---|---|
| 1 | 33 % (4/12) |
| 3 | 83 % (10/12) |
| 5 | **100 %** (12/12) |
| 10 | 100 % |
| 20 | 100 % |

> Avec seulement 4 documents dans le corpus de test, recall@5 est naturellement parfait.
> Les résultats seront plus significatifs avec les 136 documents AFAC complets.

### Fichiers générés

```
data/evaluation_results/
└── Adhésion traitement/
    ├── recall_at_k_results.csv   # Une ligne par question avec tous les recall@k
    └── recall_at_k.png           # Courbe recall@k moyenne
```

#### Structure du CSV de sortie

| Colonne | Description |
|---|---|
| `doc_name` | Nom du document évalué |
| `question_idx` | Index de la question HyQ (1-based) |
| `question` | Texte de la question (tronqué à 120 chars) |
| `source_doc` | Nom du document source attendu |
| `top1_doc` | Document retourné en première position |
| `top1_score` | Score de similarité cosinus du top-1 |
| `recall@k` | 1 si le doc source est dans le top-k, sinon 0 |

---

## VII. Passage à l'échelle — tout le corpus

`evaluate_all_docs.py` fait déjà ça : il découvre tous les documents sous `--stage5`
(un `<doc>_final.csv` + au moins une question HyQ suffit, cf. `discover_doc_names()`),
calcule Recall/Precision/nDCG/MRR@k pour chacun avec **les deux pipelines** (sémantique
seul, puis sémantique + reranker), et écrit un résumé global :

```bash
# Depuis la racine du projet afac-preprocessing
uv run python retrieval_protocol_evaluation/evaluate_all_docs.py
```

Sorties dans `--output-dir` (défaut `data/evaluation_results/`) :
- `<doc>/evaluation_results.csv` + `evaluation_results_reranked.csv` — détail par document.
- `<doc>/<métrique>_at_k.png` — courbes par document.
- `global_summary.csv` — moyennes par doc, colonnes `sem_mean_<métrique>@<k>` et
  `rer_mean_<métrique>@<k>` (c'est ce fichier que `single_retrieval_nopreprocessing/
  compare_baseline_report.py` consomme pour comparer au docling brut).
- `global_<métrique>@<k>_comparison.png`, `global_pipeline_comparison.png` — graphiques agrégés.

`evaluate.py` (ce fichier plus haut) reste utile pour inspecter un document isolé en détail
(`--question-idx` pour une seule question), mais `evaluate_all_docs.py` est l'outil à
utiliser pour le corpus complet.

---

## VIII. Rappel mathématique

### Similarité cosinus

Deux vecteurs **A** et **B** :

```
cos(A, B) = (A · B) / (‖A‖ × ‖B‖)
```

Exemple avec A = [1, 2] et B = [2, 3] :
- Produit scalaire : (1×2) + (2×3) = **8**
- ‖A‖ = √(1² + 2²) = **2.236**
- ‖B‖ = √(2² + 3²) = **3.606**
- cos(A, B) = 8 / (2.236 × 3.606) = **0.990**

Implémentation : [`sklearn.metrics.pairwise.cosine_similarity`](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.pairwise.cosine_similarity.html)

### Recall@k

```
Recall@k = 1  si doc_source ∈ {top-k résultats}
           0  sinon
```

Recall@k global = moyenne sur toutes les questions évaluées.

---

## IX. Points d'attention / TODO avec Boris / Kieran

- [ ] Valider la qualité des questions HyQ générées (revue avec Kieran)
- [ ] Confirmer la méthode de validation du dataset "Golden"
- [x] Implémenter Precision@k, nDCG@k, MRR@k — fait, cf. `metrics.py` + `evaluate_all_docs.py`
- [x] Ajouter un script batch pour tout le corpus — fait, cf. `evaluate_all_docs.py` (§VII)
- [ ] Comparer plusieurs variantes : chunking, metadata enrichi, modèles d'embedding différents
      (cf. `single_retrieval_nopreprocessing/` pour la comparaison baseline docling brut vs
      pipeline de prétraitement, déjà en place)