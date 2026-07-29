"""Cosine similarity matrix and document ranking."""
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity as sklearn_cosine_similarity


def compute_similarity_matrix(
    query_embeddings: np.ndarray,
    doc_embeddings: np.ndarray,
) -> np.ndarray:
    """Return (n_queries, n_docs) cosine similarity matrix."""
    return sklearn_cosine_similarity(query_embeddings, doc_embeddings)


def rank_docs(sim_scores: np.ndarray) -> np.ndarray:
    """Return doc indices sorted by descending similarity score."""
    return np.argsort(-sim_scores)
