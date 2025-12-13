"""
Keyword-based search engine using token overlap.
"""
import re
from typing import Dict, List, Any, Set

from .result import SearchResult


class KeywordSearcher:
    """
    Token overlap search for broad matching.
    Fallback when semantic search is unavailable or for simple queries.
    """

    # Maximum score for keyword matches (lower than regex/semantic)
    MAX_SCORE = 0.6

    def __init__(self, max_score: float = 0.6):
        """
        Args:
            max_score: Maximum score for keyword matches
        """
        self.max_score = max_score

    def _tokenize(self, text: str) -> Set[str]:
        """Extract lowercase word tokens from text."""
        return set(re.findall(r'\w+', text.lower()))

    def search(self, query: str, chunks: List[Dict[str, Any]]) -> Dict[str, SearchResult]:
        """
        Search chunks using keyword token overlap.

        Args:
            query: Search query
            chunks: List of chunk dictionaries with 'tokens' key (set of lowercase words)

        Returns:
            Dict mapping chunk_id to SearchResult
        """
        results = {}
        query_tokens = self._tokenize(query)

        if not query_tokens:
            return results

        for chunk in chunks:
            chunk_tokens = chunk.get("tokens", set())
            if not chunk_tokens:
                # Tokenize on the fly if not pre-computed
                chunk_tokens = self._tokenize(chunk["content"])

            overlap = len(query_tokens.intersection(chunk_tokens))
            if overlap > 0:
                # Normalize score by query token count
                normalized_score = (overlap / len(query_tokens)) * self.max_score
                chunk_id = chunk["id"]
                results[chunk_id] = SearchResult(
                    score=normalized_score,
                    chunk=chunk,
                    source="keyword"
                )

        return results
