"""Recall@k metric for retrieval evaluation."""


def recall_at_k(ranked_doc_names: list[str], source_doc_name: str, k: int) -> int:
    """Return 1 if source_doc_name appears in the top-k ranked docs, else 0."""
    return int(source_doc_name in ranked_doc_names[:k])


def evaluate_question(
    ranked_doc_names: list[str],
    source_doc_name: str,
    top_ks: list[int],
) -> dict[int, int]:
    """Return {k: recall_at_k} for all requested k values."""
    return {k: recall_at_k(ranked_doc_names, source_doc_name, k) for k in top_ks}
