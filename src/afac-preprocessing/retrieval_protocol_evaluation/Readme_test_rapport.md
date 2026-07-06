# Global results at k=5

| Metric | Semantic | Semantic + Reranker | Δ |
|---|---|---|---|
| Recall@5 | 0.925 | 0.982 | +0.057 |
| nDCG@5 | 0.840 | 0.892 | +0.052 |
| MRR@5 | 0.811 | 0.862 | +0.051 |

The reranker consistently improves all three ranking metrics by ~5 points across the full corpus.

# 1 — Documents split into 3 groups
## Group A — Already perfect, reranker is neutral (both ≈ 1.0 at k=5)

| Doc | sem nDCG@5 | rer nDCG@5 |
|---|---|---|
| Gestion des langues dans GEDO	| 1.000 | 1.000 |
| Globe-trotter | 1.000 | 1.000 |
| Liste des représentations suisses | 1.000 | 1.000 |
| Retrait d'adhésion par l'assuré | 1.000 | 1.000 |
| Modification date d'adhésion | 1.000 | 0.963 |

These documents are trivially retrievable — their content is unique enough that cosine similarity alone ranks them first every time. They provide no useful signal for benchmarking difficulty.

## Group B — Hard for semantic, reranker fixes them (biggest gains)

| Doc | sem nDCG@5 | rer nDCG@5 | Δ |
|---|---|---|---|
| Adhésion traitement | 0.399 | 0.798 | +0.399 |
| Pas immatriculé | 0.527 | 0.824 | +0.297 |
| NON adhésion - Refuser | 0.521	|0.663 | +0.142 |
| Liste pays UE-AELE | 0.850 | 1.000 | +0.150 |
| Demande prématurée | 0.829 | 0.954 | +0.125 |

These are the semantically ambiguous documents — their questions use vocabulary that overlaps heavily with neighbouring documents in the corpus. The embedding vector collapses these distinctions; the reranker, reading the actual resume text, discriminates correctly.

"Adhésion traitement" is the most critical case: semantic recall@1 = 17%, reranker recall@1 = 58%. Questions about this document were systematically confused with "NON adhésion" documents because both share the same procedural and legal vocabulary.

## Group C — Semantic works fine, reranker slightly regresses

| Doc | sem nDCG@5 | rer nDCG@5 | Δ |
|---|---|---|---|
| Domicilié dans les DOM-TOM, UE | 0.938 | 0.866 | -0.072 |
| Étudiant au tarif de l'AO | 0.963 |0.852	| -0.111 |
| Demande de justificatifs | 0.731|	0.674| -0.057 |
| NON adhésion - Annulation | 1.000 | 0.950 | -0.050 |

When the embedding already perfectly separates a document from its neighbours, the reranker introduces noise. This happens because the reranker reads the resume.md summaries (~700 chars), which are less discriminative than the full document embeddings for documents with already distinctive content. In a real RAG setup you'd only rerank when semantic confidence is low.

# 2 — What the metrics actually measure in your context
Recall@k answers: "Is the right document in the top-k at all?"
With 20 documents and k=5, sem recall@5 = 0.925 means 1 question in 13 doesn't find the right doc in top-5 with semantic search alone. The reranker brings this to 0.982 — nearly perfect.

nDCG@k answers: "How high is the right document ranked within top-k?"
This is the most important metric for RAG because a document ranked #1 contributes far more to generation quality than one ranked #5. The gap semantic=0.840 vs reranker=0.892 shows the reranker not only finds the right doc more often, but places it higher.

MRR@k answers: "On average, what rank does the first correct result get?"
MRR@5 sem=0.811 → average effective rank ≈ 1.23. MRR@5 rer=0.862 → average effective rank ≈ 1.16. The reranker moves the correct document about 0.07 ranks closer to position 1 on average.

Precision@k is less informative here since you have exactly 1 relevant document per question — precision@5 is always recall@5 / 5. It's only useful if you had multiple relevant chunks per question.

# 3 — Bottom line for your RAG pipeline
The reranker is worth using but conditionally: the 5 docs in Group B — especially "Adhésion traitement", "Pas immatriculé" and "NON adhésion - Refuser" — are the only ones where it makes a material difference. For the 9 docs already at or near 1.0 with semantic alone, the reranker adds latency and occasionally hurts. A practical strategy would be to always rerank, accepting the small regressions on easy docs in exchange for the large gains on the hard ones — the net result across the corpus is clearly positive.