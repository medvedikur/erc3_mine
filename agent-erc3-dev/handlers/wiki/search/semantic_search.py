"""
Semantic search engine using sentence embeddings.
"""
import re
from typing import Dict, List, Any, Optional

from .result import SearchResult


class SemanticSearcher:
    """
    Vector similarity search using sentence-transformers.
    Best for natural language queries with conceptual matching.
    """

    # Minimum query length after cleaning
    MIN_QUERY_LENGTH = 3
    # Minimum score threshold to include result
    MIN_SCORE_THRESHOLD = 0.25

    def __init__(self, model=None):
        """
        Args:
            model: SentenceTransformer model instance (optional, can be set later)
        """
        self.model = model
        self._util = None

    def set_model(self, model):
        """Set the embedding model."""
        self.model = model

    def _get_util(self):
        """Lazy import of sentence_transformers.util."""
        if self._util is None:
            try:
                from sentence_transformers import util
                self._util = util
            except ImportError:
                return None
        return self._util

    def _clean_query(self, query: str) -> str:
        """Remove regex operators from query for embedding."""
        clean = re.sub(r'[.*+?\[\](){}|^$\\]', ' ', query)
        return ' '.join(clean.split())

    def search(
        self,
        query: str,
        chunks: List[Dict[str, Any]],
        embeddings,
        top_k: int = 10
    ) -> Dict[str, SearchResult]:
        """
        Search chunks using semantic similarity.

        Args:
            query: Search query (natural language)
            chunks: List of chunk dictionaries
            embeddings: Pre-computed corpus embeddings tensor
            top_k: Maximum results to return

        Returns:
            Dict mapping chunk_id to SearchResult
        """
        results = {}

        if self.model is None or embeddings is None:
            return results

        util = self._get_util()
        if util is None:
            return results

        # Clean query for embedding
        clean_query = self._clean_query(query)
        if len(clean_query) < self.MIN_QUERY_LENGTH:
            return results

        try:
            query_emb = self.model.encode(clean_query, convert_to_tensor=True)
            hits = util.semantic_search(query_emb, embeddings, top_k=top_k * 2)

            for hit in hits[0]:
                idx = hit['corpus_id']
                if idx >= len(chunks):
                    continue

                chunk = chunks[idx]
                chunk_id = chunk["id"]
                score = hit['score']

                if score > self.MIN_SCORE_THRESHOLD:
                    results[chunk_id] = SearchResult(
                        score=score,
                        chunk=chunk,
                        source="semantic"
                    )
        except Exception:
            # Silently fail on embedding errors
            pass

        return results
