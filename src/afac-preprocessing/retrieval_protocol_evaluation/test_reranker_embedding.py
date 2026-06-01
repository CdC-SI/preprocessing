import numpy as np
from sklearn.metrics import precision_score, recall_score, ndcg_score, label_ranking_average_precision_score
import requests
from pathlib import Path
import sys
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.config import load_vlm_config

config = load_vlm_config()
CA_PATH = config["CA_PATH"]
EMBEDDING_MODEL_NAME = config["EMBEDDING_MODEL_NAME"]
EMBEDDING_URL = config["EMBEDDING_URL"]
RERANKER_URL = config["RERANKER_URL"]
RERANKER_MODEL_NAME = config["RERANKER_MODEL_NAME"]

k = 5 # pour les métriques top-k

def test_embedding_connection():
    try:
        payload = {
            "model": EMBEDDING_MODEL_NAME,
            "input": "test"
        }
        resp = requests.post(f"{EMBEDDING_URL}/v1/embeddings", 
                             json=payload, 
                             verify=CA_PATH, # Necessaire si certificat auto-signé, sinon peut être omis
                             timeout=10
                             )
        resp.raise_for_status()
        data = resp.json()
        if "data" in data and data["data"]:
            print("Embedding model connection: OK")
            return True
        else:
            print("Embedding model connection: FAIL (no data)")
            return False
    except Exception as e:
        print(f"Embedding model connection: FAIL ({e})")
        return False
    
def test_reranker_with_prompt(query, relevant_idx, documents=None):
    prefix = '<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>\n<|im_start|>user\n'
    suffix = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"

    query_template = "{prefix}<Instruct>: {instruction}\n<Query>: {query}\n"
    document_template = "<Document>: {doc}{suffix}"

    instruction = (
        "Given a user search query, determine whether the document can answer the query or not."
    )

    if documents is None:
        documents = [
            "L'AVS est le système d'assurances sociale en Suisse.",
            "L'assurance invalidité règle les questions liées à l'assujetissement.",
            "Look up https://avs-ai.ch for more info about AVS in Switzerland", 
            "Une baleine mange du krill.", 
            "How nice is the inference with vLLM!",
            "Non, tu n'est pas assujetti à l'assurance vieillesse en tant qu'indépendant."
        ]

    queries_formatted = [
        query_template.format(prefix=prefix, instruction=instruction, query=query)
    ]
    documents_formatted = [document_template.format(doc=doc, suffix=suffix) for doc in documents]
    relevant = {str(i) for i in relevant_idx}
    graded = {str(i): 1 for i in relevant_idx}

    try:
        response = requests.post(
            f"{RERANKER_URL}/score",
            json={
                "model": RERANKER_MODEL_NAME,
                "text_1": queries_formatted,
                "text_2": documents_formatted,
                "truncate_prompt_tokens": -1,
            },
            verify=CA_PATH, # Necessaire si certificat auto-signé, sinon peut être omis
            timeout=20,
        )
        response.raise_for_status()
        result = response.json()
        scores = [item["score"] for item in result["data"]]
        reranked = [{"doc_id": str(i), "score": s} for i, s in enumerate(scores)]
        reranked = sorted(reranked, key=lambda x: x["score"], reverse=True)
        reranked_topk = reranked[:k]
        all_doc_ids_topk = [doc["doc_id"] for doc in reranked_topk]
        
        # Metrics
        rr = simple_mrr_at_k(reranked_topk, relevant)
        prec = sklearn_precision_at_k(relevant, all_doc_ids_topk, k)
        rec = sklearn_recall_at_k(relevant, all_doc_ids_topk, k)
        ndcg = sklearn_ndcg_at_k(reranked_topk, graded, all_doc_ids_topk, k)

        # Debug
        print("\n--- Debug Reranker Output ---")
        print("doc_ids topk:", all_doc_ids_topk)
        print("pertinence topk:", [1 if doc_id in relevant else 0 for doc_id in all_doc_ids_topk])
        print("scores topk:", [doc["score"] for doc in reranked_topk])
        print(f"RR={rr:.4f}, precision={prec:.4f}, recall={rec:.4f}, ndcg={ndcg:.4f}")
        return rr, prec, rec, ndcg
    except Exception as e:
        print(f"Reranker prompt test: FAIL ({e})")
        return 0.0, 0.0, 0.0, 0.0
    
# Calcul des 4 métriques:
def get_query_embedding(query):
    payload = {
        "model": EMBEDDING_MODEL_NAME,
        "input": query
    }
    response = requests.post(f"{EMBEDDING_URL}/v1/embeddings", json=payload, timeout=60)
    response.raise_for_status()
    return response.json()["data"][0]["embedding"]

def remote_rerank(query, docs, server_url):
    payload = {
        "query": query,
        "documents": docs
    }
    response = requests.post(f"{server_url}/rerank", json=payload, timeout=60)
    response.raise_for_status()
    return response.json()["scores"]

def cosine_similarity(a, b):
    a = np.array(a)
    b = np.array(b)
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8)

def make_label_vectors(retrieved, relevant, all_doc_ids, graded_relevance=None):
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

def sklearn_precision_at_k(relevant, all_doc_ids, k):
    y_true = [1 if doc_id in relevant else 0 for doc_id in all_doc_ids]
    return precision_score(y_true, [1]*len(y_true), zero_division=0)

def sklearn_recall_at_k(relevant, all_doc_ids, k):
    y_true = [1 if doc_id in relevant else 0 for doc_id in all_doc_ids]
    return recall_score(y_true, [1]*len(y_true), zero_division=0)

def sklearn_ndcg_at_k(reranked, graded_relevance, all_doc_ids, k):
    y_true = [graded_relevance.get(doc_id, 0) for doc_id in all_doc_ids]
    y_score = [doc["score"] for doc in reranked]
    return ndcg_score([y_true], [y_score], k=k)

def simple_mrr_at_k(reranked_topk, relevant):
    for rank, doc in enumerate(reranked_topk, 1):
        if doc["doc_id"] in relevant:
            return 1.0 / rank
    return 0.0

# Appel dans le main
if __name__ == "__main__":
    embedding_ok = test_embedding_connection()
    all_rr = []
    all_prec = []
    all_rec = []
    all_ndcg = []
    if embedding_ok:
        queries = [
            "En tant qu'indépendant, suis-je assujeti à l'AVS ?",
            # Add more queries here
        ]
        relevant_indices_list = [
            {1, 5},  # relevant_idx for first query
            # Add relevant_idx sets for each query
        ]
        for q_idx, (query, relevant_idx) in enumerate(zip(queries, relevant_indices_list)):
            rr, prec, rec, ndcg = test_reranker_with_prompt(query, relevant_idx)
            all_rr.append(rr)
            all_prec.append(prec)
            all_rec.append(rec)
            all_ndcg.append(ndcg)
            print(f"Query {q_idx}: RR={rr:.4f}, precision={prec:.4f}, recall={rec:.4f}, ndcg={ndcg:.4f}")
        # Global metrics
        if all_rr:
            print("\n=== Global Metrics ===")
            print(f"Global MRR over {len(all_rr)} queries: {np.mean(all_rr):.4f}")
            print(f"Mean precision@{k}: {np.mean(all_prec):.4f}")
            print(f"Mean recall@{k}: {np.mean(all_rec):.4f}")
            print(f"Mean nDCG@{k}: {np.mean(all_ndcg):.4f}")
    else:
        print("Problème de connexion à au moins un modèle.")