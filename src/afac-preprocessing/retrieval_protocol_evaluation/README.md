# I/ Introduction

## 1/ Construire un jeu d’évaluation (“queries + documents pertinents”)
## 2/ Evaluer le retriever (embeddings / vector DB)
## 3/ Evaluer le reranker
### Calculer :
- Recall@k
- Precision@k
- nDCG@k
- MRR@k
Comparer les variantes de modèles / chunking / metadata / sources

Première étape, il nous faut un dataset dit "golden"

# II/ Pipeline globale
- Il faut une **Query**
- Un **Embedding model**
- Un **Vector Search** pour trouver le top N
- Un modèle de reranking **Reranker**
- Top K final
- Passer le résultat dans les métriques

# III/ Exemple de Pipeline
si on prend par exemple:
- Embeddings -> sentence-transformers https://sbert.net/
- vector DB -> FAISS https://github.com/facebookresearch/faiss
- reranker -> cross-encodre https://huggingface.co/cross-encoder
- métriques -> sklearn un sur-mesure

```bash
pip install sentence-transformers faiss-cpu numpy pandas
```

## L'objectif de cet section est :
- d'évaluer les docs AFAC avec les 4 métrics (déjà implémentées dans le script test_reranker_embedding.py)
- - pour se faire, il faut générer N questions hypothétiques par doc et les embedder (déjà réaliser ave la génération des HyQ par le script hyq_embedding_doc_modular.py)
- - fixer top K (ie 5), le plus petit possible
- - effectuer le retrieval avec semantic search + cosine similarity -> cos(hyq_e, doc_e) -> rank -> top K docs
- - évaluer les 4 métriques avec ces top k docs
Générer un csv avec les résultats pour les 4 métriques + des graphiques (bartchart ordonnés) (**import matplotlib.pyplot as plt**).

*Points à vérifier avec Kieran :*
- Comment vérifier son retrival du top K
- Comment valider le dataset "Golden" pour vérifier la véracité du résultat
- Comment contrôler la qualité des questions générées avec les Hypthotetical Questions (HyQ)

Calcule cosine similarity:
https://scikit-learn.org/stable/modules/generated/sklearn.metrics.pairwise.cosine_similarity.html

deux vecteur par example **A[1,2]** et **B[2,3]**

Produit scalaire (Dot Product) : (1 x 2) + (2 x 3) = 8
Magnitude de A : Racine carrée de ((1^2) + (2^2)) = 2.236
Magnitude de B : Racine carrée de ((2^2) + (3^2)) = 3.606
Cosine similarity = (8)/(2.236 x 3.606) = 0.990

Déroulé du projet :
le principe ici:
avec l'aide de mon pipeine d'exaction des documents de l'AFAC, je génère un CSV en sortie avec:
- CONTENT
- METADATA
- EMBEDDING

+ les INTENT et HYQ
