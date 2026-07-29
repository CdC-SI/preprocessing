"""
Retrieval metrics — Recall@k, Precision@k, nDCG@k, MRR@k.

All functions operate on corpus-order arrays (sim_scores, doc_names) rather than
pre-ranked lists, so the sklearn/scipy APIs can be used directly.

Libraries:
  sklearn.metrics.recall_score
  sklearn.metrics.precision_score
  sklearn.metrics.ndcg_score
  scipy.stats.rankdata  (for MRR)
"""
import numpy as np
from scipy.stats import rankdata
from sklearn.metrics import ndcg_score as sklearn_ndcg_score
from sklearn.metrics import precision_score, recall_score


def _y_vectors(
    sim_scores: np.ndarray,
    doc_names: list[str],
    source_doc_name: str,
    k: int,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Return (y_true, y_pred_at_k) in corpus order.

    y_true  : 1 for the source document, 0 for every other document.
    y_pred  : 1 for the top-k documents by sim_scores, 0 for the rest.
    """
    y_true = np.array([1 if name == source_doc_name else 0 for name in doc_names])
    ranks = rankdata(-sim_scores, method="ordinal")
    y_pred = (ranks <= k).astype(int)
    return y_true, y_pred


def recall_at_k(
    sim_scores: np.ndarray,
    doc_names: list[str],
    source_doc_name: str,
    k: int,
) -> float:
    """Recall@k — fraction of relevant documents retrieved in the top-k."""
    y_true, y_pred = _y_vectors(sim_scores, doc_names, source_doc_name, k)
    return float(recall_score(y_true, y_pred, zero_division=0))


def precision_at_k(
    sim_scores: np.ndarray,
    doc_names: list[str],
    source_doc_name: str,
    k: int,
) -> float:
    """Precision@k — fraction of top-k retrieved documents that are relevant."""
    y_true, y_pred = _y_vectors(sim_scores, doc_names, source_doc_name, k)
    return float(precision_score(y_true, y_pred, zero_division=0))


def ndcg_at_k(
    sim_scores: np.ndarray,
    doc_names: list[str],
    source_doc_name: str,
    k: int,
) -> float:
    """nDCG@k — normalised discounted cumulative gain at k."""
    y_true = np.array([[1.0 if name == source_doc_name else 0.0 for name in doc_names]])
    y_score = np.array([sim_scores])
    return float(sklearn_ndcg_score(y_true, y_score, k=k))


def mrr_at_k(
    sim_scores: np.ndarray,
    doc_names: list[str],
    source_doc_name: str,
    k: int,
) -> float:
    """MRR@k — reciprocal rank of the relevant document, 0 if not in top-k."""
    y_true = np.array([1 if name == source_doc_name else 0 for name in doc_names])
    ranks = rankdata(-sim_scores, method="ordinal")
    true_rank = int(ranks[np.argmax(y_true)])
    return 1.0 / true_rank if true_rank <= k else 0.0


def evaluate_all_metrics(
    sim_scores: np.ndarray,
    doc_names: list[str],
    source_doc_name: str,
    top_ks: list[int],
) -> dict[str, dict[int, float]]:
    """Return {metric: {k: score}} for all metrics and all k values."""
    return {
        "recall":    {k: recall_at_k(sim_scores, doc_names, source_doc_name, k) for k in top_ks},
        "precision": {k: precision_at_k(sim_scores, doc_names, source_doc_name, k) for k in top_ks},
        "ndcg":      {k: ndcg_at_k(sim_scores, doc_names, source_doc_name, k) for k in top_ks},
        "mrr":       {k: mrr_at_k(sim_scores, doc_names, source_doc_name, k) for k in top_ks},
    }
