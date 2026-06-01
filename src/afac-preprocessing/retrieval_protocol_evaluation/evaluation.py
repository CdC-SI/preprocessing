import os
import numpy as np
import pandas as pd
from sklearn.metrics import precision_score, recall_score, ndcg_score, label_ranking_average_precision_score
import requests
from dotenv import load_dotenv
from pathlib import Path
import certifi
import json

# Chargement de .env.test
load_dotenv()
dotenv_path = Path(__file__).resolve().parent.parent / ".env.test" # Je suis sur l'.env.test qui est le même que le .env
print("Loading dotenv from:", dotenv_path.resolve(), "exists:", dotenv_path.exists())
load_dotenv(dotenv_path=dotenv_path)

# Certificat CA personnalisé si fourni, sinon fallback sur certifi (VU avec M Gianelli, pour les autres machines, demander accès)
custom_ca = os.environ.get("VLM_CA_PEM")
if custom_ca:
    os.environ.setdefault("SSL_CERT_FILE", custom_ca)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", custom_ca)
else:
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

# Vérifcation de la présence des variables d'environnement nécessaires
VLM_URL = os.environ.get("VLM_URL", "")
VLM_MODEL_NAME = os.environ.get("VLM_MODEL_NAME", "")
if not VLM_URL:
    raise RuntimeError(
        f"VLM_URL not set. Ensure {dotenv_path} exists and contains VLM_URL or export it in the environment."
    )

print(f"VLM_URL: {VLM_URL}, \nVLM_MODEL_NAME: {VLM_MODEL_NAME}")# affiche dans la console les variables d'environnement chargées pour vérification

# Embedding 
EMBEDDING_URL = os.environ.get("EMBEDDING_URL")
EMBEDDING_MODEL_NAME = os.environ.get("EMBEDDING_MODEL_NAME")
if not EMBEDDING_URL:
    raise RuntimeError(
        f"EMBEDDING_URL not set. Ensure {dotenv_path} exists and contains EMBEDDING_URL or export it in the environment."
    )

print(f"EMBEDDING_URL: {EMBEDDING_URL}, \nEMBEDDING_MODEL_NAME: {EMBEDDING_MODEL_NAME}")

# Reranker
RERANKER_URL = os.environ.get("RERANKER_URL")
RERANKER_MODEL_NAME = os.environ.get("RERANKER_MODEL_NAME")
if not RERANKER_URL:
    raise RuntimeError(
        f"RERANKER_URL not set. Ensure {dotenv_path} exists and contains RERANKER_URL or export it in the environment."
    )

print(f"RERANKER_URL: {RERANKER_URL}, \nRERANKER_MODEL_NAME: {RERANKER_MODEL_NAME}")

k = 10  # nombre de résultats à considérer pour les métriques @k, ici 10 documents

def get_query_embedding(query):
    # Appel à l'API d'embedding pour obtenir l'embedding du query
    payload = {
        "model": EMBEDDING_MODEL_NAME,
        "input": query
    }
    response = requests.post(f"{EMBEDDING_URL}", json=payload, timeout=60)
    response.raise_for_status()
    return response.json()["data"][0]["embedding"]

def remote_rerank(query, docs, server_url):
    # Appel à l'API de reranking du serveur VLM
    payload = {
        "query": query,
        "documents": docs
    }
    response = requests.post(f"{server_url}/rerank", json=payload, timeout=60)
    response.raise_for_status()
    return response.json()["scores"]

def cosine_similarity(a, b):
    # Convertir en numpy arrays
    a = np.array(a)
    b = np.array(b)
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8)

def make_label_vectors(retrieved, relevant, all_doc_ids, graded_relevance=None):
    # Crée des vecteurs y_true et y_score pour les métriques de ranking
    # cela permet de faire du sklearn.ndcg_score 
    # même si les documents ne sont pas dans le même ordre que dans le reranking
    y_true = []
    y_score = []
    for doc_id in all_doc_ids:
        y_true.append(1 if doc_id in relevant else 0)
        if graded_relevance:
            y_score.append(graded_relevance.get(doc_id, 0))
        else:
            found = next((d for d in retrieved if d["doc_id"] == doc_id), None)
            y_score.append(found["score"] if found else 0)
    return y_true, y_score

def sklearn_precision_at_k(reranked, relevant, all_doc_ids, k):
    y_true, _ = make_label_vectors(reranked[:k], relevant, all_doc_ids)
    return precision_score(y_true, [1]*len(y_true), zero_division=0)

def sklearn_recall_at_k(reranked, relevant, all_doc_ids, k):
    y_true, _ = make_label_vectors(reranked[:k], relevant, all_doc_ids)
    return recall_score(y_true, [1]*len(y_true), zero_division=0)

def sklearn_ndcg_at_k(reranked, graded_relevance, all_doc_ids, k):
    y_true, y_score = make_label_vectors(reranked[:k], graded_relevance.keys(), all_doc_ids, graded_relevance)
    return ndcg_score([y_true], [y_score], k=k)

def sklearn_mrr_at_k(reranked, relevant, all_doc_ids, k):
    y_true, y_score = make_label_vectors(reranked[:k], relevant, all_doc_ids)
    return label_ranking_average_precision_score([y_true], [y_score])

# Charger les documents (output du pipeline)
with open("documents.json", "r", encoding="utf-8") as f:
    documents = json.load(f)

# Documents: list of dicts with keys "content", "metadata", "embedding"
for i, doc in enumerate(documents):
    doc["doc_id"] = str(i)  # ou une vraie clé unique si dispo

# 2. Charger les queries et ground truth
# Format attendu: [{"query": ..., "relevant": [doc_id, ...], "graded": {doc_id: score, ...}}]
with open("queries.json", "r", encoding="utf-8") as f:
    queries = json.load(f)

results = []

for q in queries:
    query = q["query"]
    relevant = {str(x) for x in q["relevant"]}  # set de doc_id pertinents pour ce query
    graded = {str(k): v for k, v in q.get("graded", {}).items()}

    # Embedding du query
    query_emb = get_query_embedding(query)

    # Similarité initiale (retrieval)
    for doc in documents:
        doc["score"] = cosine_similarity(query_emb, [float(x) for x in doc["embedding"].split(",")])

    # Top-N candidats pour rerank
    candidates = sorted(documents, key=lambda x: x["score"], reverse=True)[:50]

    # Rerank via serveur
    scores = remote_rerank(query, [doc["content"] for doc in candidates], VLM_URL)
    reranked = [
        {**doc, "score": score}
        for doc, score in zip(candidates, scores)
    ]
    reranked = sorted(reranked, key=lambda x: x["score"], reverse=True)
    all_doc_ids = [d["doc_id"] for d in candidates]

    metrics = {
        "query": query,
        f"recall@{k}": sklearn_recall_at_k(reranked, relevant, all_doc_ids, k),
        f"precision@{k}": sklearn_precision_at_k(reranked, relevant, all_doc_ids, k),
        f"mrr@{k}": sklearn_mrr_at_k(reranked, relevant, all_doc_ids, k),
        f"ndcg@{k}": sklearn_ndcg_at_k(reranked, graded, all_doc_ids, k)
    }
    print(metrics)
    results.append(metrics)

# Résumé global
df = pd.DataFrame(results)
print("\n=== Résumé global ===")
print(df.mean(numeric_only=True))