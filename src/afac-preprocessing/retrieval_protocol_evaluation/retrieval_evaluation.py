from sentence_transformers import CrossEncoder
import numpy as np
import faiss
import math
import pandas as pd

evaluation_dataset = [
    {
        "query": "Comment fonctionne OAuth2 ?",
        "relevant_docs": ["doc_12", "doc_45"],
        "graded_relevance": {
            "doc_12": 3,
            "doc_45": 2
        }
    },
    {
        "query": "Quelle est la différence entre Kafka et RabbitMQ ?",
        "relevant_docs": ["doc_88"],
        "graded_relevance": {
            "doc_88": 3
        }
    }
]

embedding_model = SentenceTransformer(
    "all-MiniLM-L6-v2"
)

documents = [
    {"id": "doc_12", "text": "OAuth2 est un protocole..."},
    {"id": "doc_45", "text": "Le flow authorization code..."},
]

# Embeddings
doc_text = [d["text"] for d in documents]
doc_idst = [d["id"] for d in documents]

doc_embeddings = embedding_model.encode(
    doc_text,
    normalize_embeddings=True,
    )

# Index FAISS
dimension = doc_embeddings.shape[1] # dimension des embeddings, shape[1] représente le nombre de colonnes (features) dans le tableau d'embeddings
index = faiss.IndexFlatIP(dimension) # Index pour similarité cosinus
index.add(np.array(doc_embeddings, dtype=np).astype("float32")) # Ajouter les embeddings à l'index

# Top-N 
def retrieve(query, top_k = 5): # Changer top_k pour récupérer plus ou moins de résultats
    query_embedding = embedding_model.encode(
        [query],
        normalize_embeddings=True,
    )

    scores, indices = index.search(
        np.array(query_embedding, dtype=np).astype("float32"),
        top_k
    )

    retrieved_docs = []

    for idx, score in zip(indices[0], scores[0]):
        if idx == -1: # Si aucun document n'est trouvé, FAISS retourne -1
            continue
        retrieved_docs.append({
            "doc_id": doc_idst[idx], # Récupérer l'ID du document à partir de doc_idst en utilisant l'indice
            # "text": doc_text[idx], # Récupérer le texte du document à partir de doc_text en utilisant l'indice
            "score": float(score), # Convertir le score en float pour une meilleure lisibilité
        })

    return retrieved_docs

# reranker avec cross-encoder
reranker = CrossEncoder(
    "cross-encoder/ms-marco-MiniLM-L-6-v2"
)

# rerank
def rerank(query, retrieved_docs, top_k = 5):
    pairs = []

    for doc in retrieved_docs:
        doc_text = next(
            d["text"] for d in documents
              if d["id"] == doc["doc_id"]
        )
        pairs.append((query, doc_text))

    score = reranker.predict(pairs)

    reranked = []

    for doc, score in zip(retrieved_docs, score):
        reranked.append({
            "doc_id": doc["doc_id"],
            "score": float(score),
        })

    reranked = sorted(
        reranked,
        key=lambda x: x["score"],
        reverse=True
    )

    return reranked[:top_k]

# Métriques: Voir wiki (Protocol d'évaluation retrieval) interne pour les formules et explications détaillées de chaque métrique
# Recall@k
# https://insidelearningmachines.com/precisionk_and_recallk/
# https://insidelearningmachines.com/measure-performance-of-a-classification-model/

def recall_at_k(retrieved, relevant, k):
    retrieved_k = retrieved[:k]

    retrieved_ids = {d["doc_id"] for d in retrieved_k}
    return len(retrieved_ids.intersection(relevant)) / len(relevant)

# Precision@k
def precision_at_k(retrieved, relevant, k):
    retrieved_k = retrieved[:k]

    retrieved_ids = {d["doc_id"] for d in retrieved_k}
    return len(retrieved_ids.intersection(relevant)) / k

# MRR@k
def mrr_at_k(retrieved, relevant, k):
    for rank, doc in enumerate(retrieved[:k], start=1):
        if doc["doc_id"] in relevant:
            return 1 / rank
    return 0

# nDCG@k
def ndcg_at_k(retrieved, graded_relevance, k):
    dcg = 0
    for i, doc in enumerate(retrieved[:k], start=1):
        rel = graded_relevance.get(doc["doc_id"], 0) # graded_relevance est un dict qui map les doc_id à une pertinence (0, 1, 2, ...
        dcg += (2**rel - 1) / math.log2(i + 1) # forumule DCG
    
    ideal_rels = sorted(graded_relevance.values(), reverse=True)[:k]
    idcg = 0

    for i, rel in enumerate(ideal_rels, start=1):
        idcg += (2**rel - 1) / math.log2(i + 1) # forumule IDCG

    return dcg / idcg if idcg > 0 else 0

results = []

for sample in evaluation_dataset:

    query = sample["query"]

    relevant = set(sample["relevant_docs"])

    graded = sample["graded_relevance"]

    # RETRIEVAL
    retrieved = retrieve(query, top_k=20)

    # RERANKING
    reranked = rerank(query, retrieved, top_k=10)

    metrics = {
        "query": query,

        "recall@10":
            recall_at_k(reranked, relevant, 10),

        "precision@10":
            precision_at_k(reranked, relevant, 10),

        "mrr@10":
            mrr_at_k(reranked, relevant, 10),

        "ndcg@10":
            ndcg_at_k(reranked, graded, 10)
    }

    results.append(metrics)

df = pd.DataFrame(results)
print(df.mean(numeric_only=True))
