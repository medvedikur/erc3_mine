"""
Wiki management package.

Provides:
- WikiManager: Main coordinator for wiki operations
- WikiVersionStore: File-based storage for wiki versions
- WikiSummarizer: Generate concise summaries from wiki pages
- WikiMiddleware: Middleware for context injection
- HybridSearchEngine: Combined regex/semantic/keyword search
- get_embedding_model: Thread-safe embedding model singleton
"""
from .manager import WikiManager
from .storage import WikiVersionStore, WIKI_DUMP_DIR
from .summarizer import WikiSummarizer
from .middleware import WikiMiddleware
from .embeddings import get_embedding_model, has_embeddings
from .search import HybridSearchEngine, SearchResult

__all__ = [
    'WikiManager',
    'WikiVersionStore',
    'WikiSummarizer',
    'WikiMiddleware',
    'HybridSearchEngine',
    'SearchResult',
    'get_embedding_model',
    'has_embeddings',
    'WIKI_DUMP_DIR',
]
