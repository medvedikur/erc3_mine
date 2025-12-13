"""
Search result data structures.
"""
from dataclasses import dataclass
from typing import Dict, Any


@dataclass
class SearchResult:
    """Single search result with score and source info."""
    score: float
    chunk: Dict[str, Any]
    source: str  # "regex", "semantic", or "keyword"

    @property
    def chunk_id(self) -> str:
        return self.chunk["id"]

    @property
    def content(self) -> str:
        return self.chunk["content"]

    @property
    def path(self) -> str:
        return self.chunk["path"]

    def format_output(self, max_preview: int = 500) -> str:
        """Format result for display."""
        preview = self.content[:max_preview] + "..." if len(self.content) > max_preview else self.content
        source_icon = {"regex": "[R]", "semantic": "[S]", "keyword": "[K]"}.get(self.source, "")
        return f"--- Document: {self.path} (Score: {self.score:.4f} {source_icon}) ---\n{preview}\n"
