"""
Embedding model singleton for wiki semantic search.
Thread-safe initialization for parallel execution.
"""
import threading
from typing import Optional

# Try importing sentence_transformers for local embeddings
try:
    from sentence_transformers import SentenceTransformer
    HAS_EMBEDDINGS = True
except ImportError:
    HAS_EMBEDDINGS = False
    SentenceTransformer = None

# Global singleton for embedding model (thread-safe initialization)
_embedding_model = None
_embedding_model_lock = threading.Lock()

MODEL_NAME = 'all-MiniLM-L6-v2'


def get_embedding_model() -> Optional['SentenceTransformer']:
    """
    Get or create the global embedding model instance.
    Thread-safe singleton pattern.

    Returns:
        SentenceTransformer model or None if not available
    """
    global _embedding_model
    if _embedding_model is not None:
        return _embedding_model

    with _embedding_model_lock:
        # Double-check after acquiring lock
        if _embedding_model is not None:
            return _embedding_model

        if not HAS_EMBEDDINGS:
            return None

        try:
            print(f"Initializing Local Embedding Model ({MODEL_NAME})...")
            _embedding_model = SentenceTransformer(MODEL_NAME)
            return _embedding_model
        except Exception as e:
            print(f"Failed to load embedding model: {e}")
            return None


def has_embeddings() -> bool:
    """Check if embedding support is available."""
    return HAS_EMBEDDINGS
