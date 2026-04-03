from typing import List

def calculate_precision_at_k(retrieved_ids: List[str], relevant_ids: List[str], k: int) -> float:
    """Calculates Precision@K."""
    if not retrieved_ids:
        return 0.0
    k = min(k, len(retrieved_ids))
    retrieved_k = retrieved_ids[:k]
    relevant_set = set(relevant_ids)
    
    hits = sum(1 for doc_id in retrieved_k if doc_id in relevant_set)
    return hits / k

def calculate_recall_at_k(retrieved_ids: List[str], relevant_ids: List[str], k: int) -> float:
    """Calculates Recall@K."""
    if not relevant_ids:
        return 0.0
    k = min(k, len(retrieved_ids))
    retrieved_k = retrieved_ids[:k]
    relevant_set = set(relevant_ids)
    
    hits = sum(1 for doc_id in retrieved_k if doc_id in relevant_set)
    return hits / len(relevant_ids)

def calculate_mrr(retrieved_ids: List[str], relevant_ids: List[str]) -> float:
    """Calculates Mean Reciprocal Rank (MRR)."""
    relevant_set = set(relevant_ids)
    for i, doc_id in enumerate(retrieved_ids):
        if doc_id in relevant_set:
            return 1.0 / (i + 1)
    return 0.0
