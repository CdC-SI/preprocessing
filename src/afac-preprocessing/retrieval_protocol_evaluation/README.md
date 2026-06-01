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
