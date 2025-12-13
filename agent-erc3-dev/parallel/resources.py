"""
Thread-local resources for parallel execution.

Provides thread-safe access to resources that need to be isolated
per-thread to avoid race conditions.
"""

import threading
import requests

from handlers.wiki import WikiManager


# Thread-local storage
_thread_local = threading.local()


def get_thread_wiki_manager() -> WikiManager:
    """
    Get or create a WikiManager for the current thread.

    Each thread needs its own WikiManager because:
    - WikiManager has mutable state (current_sha1, pages, chunks, embeddings)
    - Two tasks running in parallel might have different wiki versions
    - If they share WikiManager, sync() calls would overwrite each other's state

    However, the DISK CACHE is shared and thread-safe:
    - WikiVersionStore saves each version to wiki_dump/{sha1}/
    - Multiple threads reading the same sha1 just read the same files
    - Multiple threads downloading different sha1s write to different dirs
    """
    if not hasattr(_thread_local, 'wiki_manager'):
        _thread_local.wiki_manager = WikiManager()
    return _thread_local.wiki_manager


def get_thread_session() -> requests.Session:
    """
    Get or create a requests.Session for the current thread.

    requests.Session is NOT thread-safe, so each thread needs its own.
    The session handles connection pooling efficiently within the thread.
    """
    if not hasattr(_thread_local, 'session'):
        _thread_local.session = requests.Session()
    return _thread_local.session
