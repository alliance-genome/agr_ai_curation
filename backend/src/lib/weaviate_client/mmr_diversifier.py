"""MMR Diversifier for V5 Weaviate implementation."""

import numpy as np
from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

def mmr_diversify(
    chunks: List[Dict[str, Any]],
    lambda_param: float = 0.5,
    top_k: int = 10,
    vector_field: str = "_vector"
) -> List[Dict[str, Any]]:
    """
    Apply Maximal Marginal Relevance for result diversification.

    Args:
        chunks: Search results with vectors and scores
        lambda_param: Balance (0=diversity, 1=relevance)
        top_k: Number of diverse results to return
        vector_field: Key where vectors are stored in chunks

    Returns:
        Diversified subset of chunks
    """
    if not chunks or top_k <= 0:
        return []

    # Check if we have vectors
    chunks_with_vectors = [c for c in chunks if vector_field in c]
    if not chunks_with_vectors:
        logger.debug("No vectors for MMR, returning top by score")
        return sorted(chunks, key=lambda x: x.get('score', 0), reverse=True)[:top_k]

    # Normalize scores to [0,1] for consistent MMR calculation
    scores = np.array([c.get('score', 0) for c in chunks_with_vectors])
    if scores.max() > 0:
        scores = scores / scores.max()

    selected_indices = []
    remaining_indices = list(range(len(chunks_with_vectors)))

    while remaining_indices and len(selected_indices) < top_k:
        if not selected_indices:
            # First selection: highest normalized score
            best_idx = np.argmax(scores[remaining_indices])
            best_idx = remaining_indices[best_idx]
        else:
            # MMR selection
            best_mmr = -float('inf')
            best_idx = None

            for idx in remaining_indices:
                # Relevance component
                relevance = scores[idx]

                # Diversity component (max similarity to selected)
                max_sim = 0.0
                for sel_idx in selected_indices:
                    vec1_raw = chunks_with_vectors[idx][vector_field]
                    vec2_raw = chunks_with_vectors[sel_idx][vector_field]
                    # Handle Weaviate returning vectors as dict {"default": [...]} or raw list
                    if isinstance(vec1_raw, dict):
                        vec1_raw = vec1_raw.get("default") or list(vec1_raw.values())[0]
                    if isinstance(vec2_raw, dict):
                        vec2_raw = vec2_raw.get("default") or list(vec2_raw.values())[0]
                    vec1 = np.array(vec1_raw)
                    vec2 = np.array(vec2_raw)

                    # Cosine similarity
                    sim = np.dot(vec1, vec2) / (np.linalg.norm(vec1) * np.linalg.norm(vec2) + 1e-10)
                    max_sim = max(max_sim, sim)

                # MMR score
                mmr = lambda_param * relevance - (1 - lambda_param) * max_sim

                if mmr > best_mmr:
                    best_mmr = mmr
                    best_idx = idx

        if best_idx is not None:
            selected_indices.append(best_idx)
            remaining_indices.remove(best_idx)

    # Return selected chunks without vectors
    result = []
    for idx in selected_indices:
        chunk = chunks_with_vectors[idx].copy()
        chunk.pop(vector_field, None)  # Remove internal vector field
        result.append(chunk)

    logger.debug(f"MMR selected {len(result)} diverse results from {len(chunks)} candidates")
    return result