"""
Wiki manager - main coordinator for wiki operations.
"""
import re
from typing import Dict, List, Optional, Any

from erc3.erc3 import client

from .storage import WikiVersionStore, WIKI_DUMP_DIR
from .summarizer import WikiSummarizer
from .embeddings import get_embedding_model
from .search import HybridSearchEngine


class WikiManager:
    """
    Manages the local cache of the company wiki with version history.
    All versions are stored in wiki_dump/{sha1}/ folders.

    Coordinates:
    - Version storage and caching
    - Page summarization
    - Hybrid search (Regex + Semantic + Keyword)
    """

    def __init__(self, api: Optional[client.Erc3Client] = None, base_dir: str = WIKI_DUMP_DIR):
        self.api = api
        self.base_dir = base_dir
        self.current_sha1: str = ""
        self.pages: Dict[str, str] = {}
        self.summaries: Dict[str, str] = {}
        self.chunks: List[Dict[str, Any]] = []
        self.corpus_embeddings = None

        # Track wiki changes for dynamic injection
        self._last_synced_sha1: Optional[str] = None
        self._sha1_change_count: int = 0

        # Initialize components
        self.store = WikiVersionStore(base_dir=base_dir)
        self.model = get_embedding_model()
        self.search_engine = HybridSearchEngine(embedding_model=self.model)

    def set_api(self, api: client.Erc3Client):
        """Set the API client for wiki operations."""
        self.api = api

    def sync(self, reported_sha1: str) -> bool:
        """
        Check if sync is needed and update if so.

        Args:
            reported_sha1: Wiki hash reported by the server

        Returns:
            True if wiki hash CHANGED (agent should re-read critical docs)
            False if wiki was already at this version
        """
        if not reported_sha1:
            return False

        if self.current_sha1 != reported_sha1:
            print(f"Wiki Sync: Hash changed ({self.current_sha1[:8] if self.current_sha1 else 'none'} -> {reported_sha1[:8]})")

            # Track wiki changes for debugging
            if self._last_synced_sha1 and self._last_synced_sha1 != reported_sha1:
                self._sha1_change_count += 1
                print(f"  Wiki changed! ({self._sha1_change_count} times this session)")

            self._last_synced_sha1 = reported_sha1

            # Check if we already have this version cached
            if self.store.version_exists(reported_sha1):
                print(f"   Version found in local cache. Loading...")
                self._load_from_cache(reported_sha1)
            else:
                print(f"   New version. Downloading from API...")
                self._download_and_save(reported_sha1)

            old_sha1 = self.current_sha1
            self.current_sha1 = reported_sha1

            # Return True only if we had a previous version (not first load)
            return old_sha1 != ""

        return False

    def _load_from_cache(self, sha1: str):
        """Load a wiki version from local cache."""
        self.pages = self.store.get_pages(sha1)
        self.chunks, self.corpus_embeddings = self.store.get_chunks(sha1)
        self.store.set_current(sha1)

        # Load summaries from cache, or generate if not cached
        self.summaries = self.store.get_summaries(sha1)
        if not self.summaries and self.pages:
            print(f"Generating summaries (not in cache)...")
            self.summaries = WikiSummarizer.generate_all_summaries(self.pages)
            self.store.save_summaries(sha1, self.summaries)

        # Generate chunks if not cached (needed for search)
        if not self.chunks and self.pages:
            print(f"Generating chunks (not in cache)...")
            self._reindex()
            self.store.save_chunks(sha1, self.chunks, self.corpus_embeddings)

        print(f"Wiki loaded from cache: {len(self.pages)} pages, {len(self.chunks)} chunks, {len(self.summaries)} summaries")

    def _download_and_save(self, sha1: str):
        """Download wiki from API and save to local cache."""
        if not self.api:
            print("Wiki Sync Failed: No API client set.")
            return

        try:
            # 1. List all pages
            list_resp = self.api.list_wiki()
            actual_sha1 = list_resp.sha1

            # 2. Download all pages
            self.pages = {}
            for path in list_resp.paths:
                print(f"   Downloading {path}...")
                load_resp = self.api.load_wiki(path)
                self.pages[path] = load_resp.content

            # 3. Save to cache
            self.store.save_version(actual_sha1, list_resp.paths, self.pages)

            # 4. Generate summaries for all pages and save to cache
            self.summaries = WikiSummarizer.generate_all_summaries(self.pages)
            self.store.save_summaries(actual_sha1, self.summaries)
            print(f"Generated and cached summaries for {len(self.summaries)} pages")

            # 5. Index/Chunk pages
            self._reindex()

            # 6. Save chunks to cache
            self.store.save_chunks(actual_sha1, self.chunks, self.corpus_embeddings)

            self.current_sha1 = actual_sha1
            print(f"Wiki Sync Complete: {len(self.pages)} pages saved to wiki_dump/{actual_sha1[:16]}/")

        except Exception as e:
            print(f"Wiki Sync Failed: {e}")
            import traceback
            traceback.print_exc()

    def _reindex(self):
        """Split pages into chunks for search and compute embeddings."""
        self.chunks = []
        for path, content in self.pages.items():
            paragraphs = content.split('\n\n')
            for i, p in enumerate(paragraphs):
                clean_p = p.strip()
                if not clean_p:
                    continue
                self.chunks.append({
                    "content": clean_p,
                    "path": path,
                    "id": f"{path}#{i}",
                    "tokens": set(re.findall(r'\w+', clean_p.lower()))
                })

        # Compute embeddings if model is available
        if self.model and self.chunks:
            print(f"Computing embeddings for {len(self.chunks)} chunks...")
            texts = [c["content"] for c in self.chunks]
            try:
                self.corpus_embeddings = self.model.encode(
                    texts, convert_to_tensor=True, show_progress_bar=False
                )
            except Exception as e:
                print(f"Embedding computation failed: {e}")
                self.corpus_embeddings = None

    def search(self, query: str, top_k: int = 5, sha1: Optional[str] = None) -> str:
        """
        Hybrid Search: Regex + Semantic + Keyword.

        Args:
            query: Search query
            top_k: Number of results
            sha1: Optional wiki version to search (defaults to current)

        Returns:
            Formatted search results string
        """
        # Determine which chunks/embeddings to use
        target_sha1 = sha1 or self.current_sha1

        if sha1 and sha1 != self.current_sha1:
            if self.store.version_exists(sha1):
                chunks, embeddings = self.store.get_chunks(sha1)
            else:
                return f"Wiki version {sha1[:8]} not found in cache."
        else:
            chunks = self.chunks
            embeddings = self.corpus_embeddings

        if not chunks:
            return "Wiki not loaded yet or empty."

        # Execute hybrid search
        results = self.search_engine.search(query, chunks, embeddings, top_k)
        return self.search_engine.format_results(results, query)

    def get_context_summary(self) -> str:
        """Return a summary for system prompt."""
        if not self.pages:
            return "Wiki not loaded yet."

        modes = self.search_engine.get_available_modes()
        mode_str = " + ".join(modes)
        return f"Wiki: {', '.join(self.pages.keys())} (Search: {mode_str})"

    def get_current_sha1(self) -> str:
        """Get current wiki hash for tools to pass to search."""
        return self.current_sha1

    def list_versions(self) -> List[Dict[str, Any]]:
        """List all cached wiki versions."""
        return self.store.get_all_versions()

    def get_critical_docs(self) -> str:
        """
        Return SUMMARIES of critical policy documents for context injection.
        Uses pre-generated summaries to save context space while preserving key info.
        Agent can use wiki_load() or wiki_search() for full details.
        """
        critical_paths = ["rulebook.md", "merger.md", "hierarchy.md"]

        docs = []
        for path in critical_paths:
            if path in self.pages:
                # Use pre-generated summary if available, otherwise generate on the fly
                if path in self.summaries:
                    summary = self.summaries[path]
                else:
                    summary = WikiSummarizer.generate_summary(self.pages[path], path)
                docs.append(f"=== {path} (SUMMARY) ===\n{summary}")

        if not docs:
            return ""

        return "\n\n".join(docs)

    def get_summary(self, path: str) -> Optional[str]:
        """Get summary for a specific wiki page."""
        if path in self.summaries:
            return self.summaries[path]
        if path in self.pages:
            return WikiSummarizer.generate_summary(self.pages[path], path)
        return None

    def _normalize_path(self, path: str) -> str:
        """Normalize path for lookup (handle both slash and underscore formats)."""
        # Try original path first
        if path in self.pages:
            return path
        # Try with slash -> underscore conversion
        normalized = path.replace("/", "_")
        if normalized in self.pages:
            return normalized
        # Try with underscore -> slash conversion
        normalized = path.replace("_", "/")
        if normalized in self.pages:
            return normalized
        return path

    def has_page(self, path: str) -> bool:
        """Check if a wiki page exists."""
        normalized = self._normalize_path(path)
        return normalized in self.pages

    def get_page(self, path: str) -> Optional[str]:
        """Get a wiki page content directly."""
        normalized = self._normalize_path(path)
        return self.pages.get(normalized)
