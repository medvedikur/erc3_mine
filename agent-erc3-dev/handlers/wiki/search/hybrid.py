"""
Hybrid search engine combining regex, semantic, and keyword search.
"""
from typing import Dict, List, Any, Optional

from .result import SearchResult
from .regex_search import RegexSearcher
from .semantic_search import SemanticSearcher
from .keyword_search import KeywordSearcher


class HybridSearchEngine:
    """
    Hybrid Search combining three streams:
    1. REGEX: Pattern matching for structured queries
    2. SEMANTIC: Vector similarity using sentence-transformers (if available)
    3. KEYWORD: Token overlap fallback for broad matching

    Results are merged, deduplicated by chunk ID, and ranked by score.
    """

    def __init__(self, embedding_model=None):
        """
        Args:
            embedding_model: SentenceTransformer model for semantic search (optional)
        """
        self.regex_searcher = RegexSearcher()
        self.semantic_searcher = SemanticSearcher(model=embedding_model)
        self.keyword_searcher = KeywordSearcher()

    def set_embedding_model(self, model):
        """Set or update the embedding model for semantic search."""
        self.semantic_searcher.set_model(model)

    def search(
        self,
        query: str,
        chunks: List[Dict[str, Any]],
        embeddings=None,
        top_k: int = 5
    ) -> List[SearchResult]:
        """
        Execute hybrid search across all three engines.

        Args:
            query: Search query (may contain regex syntax)
            chunks: List of chunk dictionaries
            embeddings: Pre-computed corpus embeddings for semantic search
            top_k: Maximum results to return

        Returns:
            List of SearchResult sorted by score (highest first)
        """
        if not chunks:
            return []

        # Collect results from all engines, keyed by chunk_id
        all_results: Dict[str, SearchResult] = {}

        # Stream 1: Regex Search (highest priority for pattern matches)
        regex_results = self.regex_searcher.search(query, chunks)
        for chunk_id, result in regex_results.items():
            all_results[chunk_id] = result

        # Stream 2: Semantic Search (if embeddings available)
        semantic_results = self.semantic_searcher.search(
            query, chunks, embeddings, top_k=top_k * 2
        )
        for chunk_id, result in semantic_results.items():
            # Only add if not already found by regex, or if semantic score is higher
            if chunk_id not in all_results or all_results[chunk_id].score < result.score:
                all_results[chunk_id] = result

        # Stream 3: Keyword Search (fallback)
        keyword_results = self.keyword_searcher.search(query, chunks)
        for chunk_id, result in keyword_results.items():
            # Only add if not found by higher-priority searches
            if chunk_id not in all_results:
                all_results[chunk_id] = result

        # Sort by score and return top_k
        sorted_results = sorted(all_results.values(), key=lambda x: x.score, reverse=True)
        return sorted_results[:top_k]

    def format_results(self, results: List[SearchResult], query: str) -> str:
        """
        Format search results as string for agent consumption.

        Args:
            results: List of SearchResult
            query: Original query (for no-results message)

        Returns:
            Formatted string with all results
        """
        if not results:
            return f"No matches found for '{query}' in wiki."

        output = []
        for result in results:
            output.append(result.format_output())

        return "\n".join(output)

    def get_available_modes(self) -> List[str]:
        """Return list of available search modes."""
        modes = ["Regex"]
        if self.semantic_searcher.model is not None:
            modes.append("Semantic")
        modes.append("Keyword")
        return modes
