"""
Regex-based search engine for wiki content.
"""
import re
from typing import Dict, List, Any

from .result import SearchResult


class RegexSearcher:
    """
    Pattern matching search using regular expressions.
    Best for structured queries with operators like .*, |, etc.
    """

    # Characters that indicate regex syntax
    REGEX_OPERATORS = r'.*+?[](){}|^$\\'

    def __init__(self, score: float = 0.95):
        """
        Args:
            score: Fixed score for regex matches (high priority)
        """
        self.score = score

    def has_regex_syntax(self, query: str) -> bool:
        """Check if query contains regex operators."""
        return any(c in query for c in self.REGEX_OPERATORS)

    def search(self, query: str, chunks: List[Dict[str, Any]]) -> Dict[str, SearchResult]:
        """
        Search chunks using regex pattern matching.

        Args:
            query: Search query (may contain regex syntax)
            chunks: List of chunk dictionaries with 'id' and 'content' keys

        Returns:
            Dict mapping chunk_id to SearchResult
        """
        results = {}

        if not self.has_regex_syntax(query):
            return results

        try:
            pattern = re.compile(query, re.IGNORECASE)
            for chunk in chunks:
                if pattern.search(chunk["content"]):
                    chunk_id = chunk["id"]
                    results[chunk_id] = SearchResult(
                        score=self.score,
                        chunk=chunk,
                        source="regex"
                    )
        except re.error:
            # Invalid regex pattern, return empty results
            pass

        return results
