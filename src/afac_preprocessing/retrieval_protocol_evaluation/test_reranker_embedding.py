"""Banc de test manuel embedding + reranker.

Lot 9 : ``sys.path.insert`` + ``utils.config`` remplacés par ``Settings``, et
``requests`` par ``httpx.AsyncClient`` — tous les appels réseau du dépôt sont
asynchrones (exigence métier).

Lancement : ``uv run python -m afac_preprocessing.retrieval_protocol_evaluation.test_reranker_embedding``
(malgré son nom, ce n'est pas un test pytest : ``testpaths`` ne couvre que ``tests/``).
"""

import asyncio

import httpx
import numpy as np
from sklearn.metrics import ndcg_score, precision_score, recall_score

from ..settings import Settings, default_dotenv

# Résolution PARESSEUSE : la config était lue au niveau module, ce qui rendait
# le simple import impossible sans VLM_URL. Les constantes restent des noms de
# module (le corps des fonctions est inchangé), remplies par _init_config().
CA_PATH = ""
EMBEDDING_MODEL_NAME = ""
EMBEDDING_URL = ""
RERANKER_URL = ""
RERANKER_MODEL_NAME = ""


def _init_config() -> None:
    global CA_PATH, EMBEDDING_MODEL_NAME, EMBEDDING_URL, RERANKER_URL, RERANKER_MODEL_NAME
    settings = Settings.from_dotenv(default_dotenv())
    CA_PATH = settings.resolved_ca_path
    EMBEDDING_MODEL_NAME = settings.embedding_model_name
    EMBEDDING_URL = str(settings.embedding_url).rstrip("/") if settings.embedding_url else ""
    RERANKER_URL = str(settings.reranker_url).rstrip("/") if settings.reranker_url else ""
    RERANKER_MODEL_NAME = settings.reranker_model_name

k = 5 # for top-k metrics


async def test_embedding_connection() -> bool:
    """
    - Tests the connection to the embedding API by sending a test request and checking the response.

    :return: True if the embedding API responded successfully with data, False otherwise.
    :rtype: bool
    """
    try:
        payload = {
            "model": EMBEDDING_MODEL_NAME,
            "input": "test"
        }
        async with httpx.AsyncClient(verify=CA_PATH, timeout=10) as client:
            resp = await client.post(f"{EMBEDDING_URL}/v1/embeddings", json=payload)
            resp.raise_for_status()
            data = resp.json()
        if "data" in data and data["data"]:
            print("Embedding model connection: OK")
            return True
        print("Embedding model connection: FAIL (no data)")
        return False
    except Exception as e:
        print(f"Embedding model connection: FAIL ({e})")
        return False
    
async def test_reranker_with_prompt(query, relevant_idx, documents=None) -> tuple[float, float, float, float]:
    """
    - Tests the connection to the reranking API by sending a test request with a formatted prompt and checking the response.
    - Formats the prompt for the reranker by including a clear instruction, the user's query, and the documents to evaluate.
    - Computes performance metrics (MRR, precision, recall, nDCG) by comparing the reranker's results with the provided relevant indices.

    :param query: The user search query to evaluate against the documents.
    :param relevant_idx: Iterable of indices (into ``documents``) that are considered relevant for this query.
    :param documents: Optional list of candidate document strings to rerank. If None, a default set of test documents is used.
    :return: A tuple of (reciprocal rank, precision@k, recall@k, nDCG@k) computed on the top-k reranked results.
    :rtype: tuple[float, float, float, float]
    """
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
        async with httpx.AsyncClient(verify=CA_PATH, timeout=20) as client:
            response = await client.post(
                f"{RERANKER_URL}/score",
                json={
                    "model": RERANKER_MODEL_NAME,
                    "text_1": queries_formatted,
                    "text_2": documents_formatted,
                    "truncate_prompt_tokens": -1,
                },
            )
            response.raise_for_status()
            result = response.json()
        scores = [item["score"] for item in result["data"]]
        reranked = [{"doc_id": str(i), "score": s} for i, s in enumerate(scores)]
        reranked = sorted(reranked, key=lambda x: x["score"], reverse=True)
        reranked_topk = reranked[:k]
        all_doc_ids_topk = [doc["doc_id"] for doc in reranked_topk]
        
        # Metrics
        # rr = simple_mrr_at_k(reranked_topk, relevant)
        rr = reciprocal_rank_at_k(reranked_topk, relevant)
        prec = sklearn_precision_at_k(relevant, all_doc_ids_topk, k)
        rec = sklearn_recall_at_k(relevant, all_doc_ids_topk, k)
        ndcg = sklearn_ndcg_at_k(reranked_topk, graded, all_doc_ids_topk, k)

        # Debug
        print("\n--- Debug Reranker Output ---")
        print("doc_ids topk:", all_doc_ids_topk)
        print("relevance topk:", [1 if doc_id in relevant else 0 for doc_id in all_doc_ids_topk])
        print("scores topk:", [doc["score"] for doc in reranked_topk])
        print(f"RR={rr:.4f}, precision={prec:.4f}, recall={rec:.4f}, ndcg={ndcg:.4f}")
        return rr, prec, rec, ndcg
    except Exception as e:
        print(f"Reranker prompt test: FAIL ({e})")
        return 0.0, 0.0, 0.0, 0.0
    

async def get_query_embedding(query) -> list[float]:
    """
    - Sends a request to the embedding API to obtain the embedding of the user query.
    - Formats the request with the embedding model name and the query text, then sends a POST request to the embedding endpoint.
    - Processes the response to extract the query embedding, which is returned as a list of floats.

    :param query: The user search query to embed.
    :return: The embedding vector of the query.
    :rtype: list[float]
    """
    payload = {
        "model": EMBEDDING_MODEL_NAME,
        "input": query
    }
    async with httpx.AsyncClient(verify=CA_PATH, timeout=60) as client:
        response = await client.post(f"{EMBEDDING_URL}/v1/embeddings", json=payload)
        response.raise_for_status()
        return response.json()["data"][0]["embedding"]


async def remote_rerank(query, docs, server_url) -> list[float]:
    """
    - Sends a request to the reranking API to obtain the relevance scores of the documents with respect to the query.
    - Formats the request with the user query and the documents to evaluate, then sends a POST request to the reranking endpoint.
    - Processes the response to extract the relevance scores, which are returned as a list of floats.

    :param query: The user search query.
    :param docs: The list of candidate documents to score against the query.
    :param server_url: The base URL of the reranking server to call.
    :return: The list of relevance scores, one per document, in the same order as ``docs``.
    :rtype: list[float]
    """
    payload = {
        "query": query,
        "documents": docs
    }
    async with httpx.AsyncClient(verify=CA_PATH, timeout=60) as client:
        response = await client.post(f"{server_url}/rerank", json=payload)
        response.raise_for_status()
        return response.json()["scores"]


def cosine_similarity(a, b) -> float:
    """
    - Computes the cosine similarity between two embedding vectors.
    - Converts the inputs into numpy arrays, then uses the cosine similarity formula to compute the similarity between the two vectors,
    adding a small value (1e-8) to the denominator to avoid division by zero.

    :param a: The first embedding vector.
    :param b: The second embedding vector.
    :return: The cosine similarity between ``a`` and ``b``.
    :rtype: float
    """
    a = np.array(a)
    b = np.array(b)
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8)


def make_label_vectors(retrieved, relevant, all_doc_ids, graded_relevance=None) -> tuple[list[int], list[float]]:
    """
    - Creates the label vectors (y_true) and score vectors (y_score) used to compute the evaluation metrics.
    - Iterates over all evaluated documents (all_doc_ids)
    and builds y_true by indicating whether each document is relevant (1) or not (0) based on the set of relevant indices.
    - For y_score, if graded relevance values are provided, uses those values as scores,
    otherwise uses the relevance scores returned by the reranker.

    :param retrieved: The list of reranked documents, each a dict with ``doc_id`` and ``score`` keys.
    :param relevant: The set of document ids considered relevant.
    :param all_doc_ids: The full list of document ids to build the label/score vectors for.
    :param graded_relevance: Optional mapping from document id to a graded relevance score. If not provided, scores from ``retrieved`` are used instead.
    :return: A tuple (y_true, y_score) where y_true holds binary relevance labels and y_score holds the corresponding scores.
    :rtype: tuple[list[int], list[float]]
    """
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


# def sklearn_precision_at_k(relevant, all_doc_ids, k):
#     y_true = [1 if doc_id in relevant else 0 for doc_id in all_doc_ids]
#     return precision_score(y_true, [1]*len(y_true), zero_division=0)

def sklearn_precision_at_k(relevant, ranked_doc_ids, k) -> float:
    top_k = ranked_doc_ids[:k]
    y_true = [1 if doc_id in relevant else 0 for doc_id in top_k]
    y_pred = [1] * len(top_k)

    return precision_score(y_true, y_pred, zero_division=0)

# def sklearn_recall_at_k(relevant, all_doc_ids, k):
#     y_true = [1 if doc_id in relevant else 0 for doc_id in all_doc_ids]
#     return recall_score(y_true, [1]*len(y_true), zero_division=0)

def sklearn_recall_at_k(relevant, ranked_doc_ids, k) -> float:
    top_k = set(ranked_doc_ids[:k])
    universe = set(relevant) | top_k
    y_true = [1 if doc_id in relevant else 0 for doc_id in universe]
    y_pred = [1 if doc_id in top_k else 0 for doc_id in universe]

    return recall_score(y_true, y_pred, zero_division=0)


# def sklearn_ndcg_at_k(reranked, graded_relevance, all_doc_ids, k):
#     y_true = [graded_relevance.get(doc_id, 0) for doc_id in all_doc_ids]
#     y_score = [doc["score"] for doc in reranked]
#     return ndcg_score([y_true], [y_score], k=k)

def sklearn_ndcg_at_k(
    reranked,
    graded_relevance,
    all_doc_ids,
    k,
) -> float:
    score_by_id = {
        doc["doc_id"]: doc["score"]
        for doc in reranked
    }

    y_true = [
        graded_relevance.get(doc_id, 0)
        for doc_id in all_doc_ids
    ]

    y_score = [
        score_by_id.get(doc_id, 0.0)
        for doc_id in all_doc_ids
    ]

    return ndcg_score([y_true], [y_score], k=k)


# def simple_mrr_at_k(reranked_topk, relevant):
#     for rank, doc in enumerate(reranked_topk, 1):
#         if doc["doc_id"] in relevant:
#             return 1.0 / rank
#     return 0.0


def reciprocal_rank_at_k(reranked_topk, relevant) -> float:
    relevant = set(relevant)

    for rank, doc in enumerate(reranked_topk, start=1):
        if doc["doc_id"] in relevant:
            return 1.0 / rank

    return 0.0

# Call in main
async def main() -> None:
    _init_config()
    embedding_ok = await test_embedding_connection()
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
        for q_idx, (query, relevant_idx) in enumerate(zip(queries, relevant_indices_list, strict=False)):
            rr, prec, rec, ndcg = await test_reranker_with_prompt(query, relevant_idx)
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
        print("Connection problem with at least one model.")


if __name__ == "__main__":
    asyncio.run(main())
