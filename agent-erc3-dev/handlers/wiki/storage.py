"""
File-based storage for wiki versions.
Each version stored in wiki_dump/{sha1_prefix}/ folder.
"""
import os
import json
import threading
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime

from .embeddings import has_embeddings

if has_embeddings():
    import numpy as np
    import torch
else:
    np = None
    torch = None


# Default storage paths
WIKI_DUMP_DIR = "wiki_dump"


class WikiVersionStore:
    """
    File-based storage for wiki versions.
    Each version stored in wiki_dump/{sha1_prefix}/ folder.

    Uses class-level cache for pages/chunks to avoid repeated disk I/O
    when multiple WikiManager instances load the same version (parallel mode).
    """
    # Class-level cache shared across all instances (thread-safe)
    _pages_cache: Dict[str, Dict[str, str]] = {}
    _chunks_cache: Dict[str, Tuple[List[Dict[str, Any]], Optional[Any]]] = {}
    _summaries_cache: Dict[str, Dict[str, str]] = {}
    _cache_lock = threading.Lock()

    def __init__(self, base_dir: str = WIKI_DUMP_DIR):
        self.base_dir = base_dir
        self.versions_index = os.path.join(base_dir, "versions.json")
        os.makedirs(base_dir, exist_ok=True)
        self._load_index()

    def _load_index(self):
        """Load versions index from JSON file."""
        if os.path.exists(self.versions_index):
            try:
                with open(self.versions_index, 'r', encoding='utf-8') as f:
                    self.index = json.load(f)
            except Exception as e:
                print(f"Failed to load wiki index: {e}")
                self.index = {"versions": {}, "current": None}
        else:
            self.index = {"versions": {}, "current": None}

    def _save_index(self):
        """Save versions index to JSON file."""
        try:
            with open(self.versions_index, 'w', encoding='utf-8') as f:
                json.dump(self.index, f, indent=2)
        except Exception as e:
            print(f"Failed to save wiki index: {e}")

    def _get_version_dir(self, sha1: str) -> str:
        """Get directory path for a wiki version (uses first 16 chars of hash)."""
        return os.path.join(self.base_dir, sha1[:16])

    def version_exists(self, sha1: str) -> bool:
        """Check if a wiki version already exists."""
        return sha1 in self.index["versions"]

    def save_version(self, sha1: str, paths: List[str], pages: Dict[str, str]):
        """Save a new wiki version to files."""
        version_dir = self._get_version_dir(sha1)
        os.makedirs(version_dir, exist_ok=True)

        # Save metadata
        metadata = {
            "sha1": sha1,
            "paths": paths,
            "created_at": datetime.now().isoformat()
        }
        with open(os.path.join(version_dir, "metadata.json"), 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2)

        # Save pages as individual files
        for path, content in pages.items():
            safe_name = path.replace("/", "_").replace("\\", "_")
            if not safe_name.endswith(".md"):
                safe_name += ".md"

            with open(os.path.join(version_dir, safe_name), 'w', encoding='utf-8') as f:
                f.write(f"<!-- PATH: {path} -->\n")
                f.write(f"<!-- SHA1: {sha1} -->\n\n")
                f.write(content)

        # Update index
        self.index["versions"][sha1] = {
            "dir": sha1[:16],
            "created_at": metadata["created_at"],
            "paths": paths
        }
        self.index["current"] = sha1
        self._save_index()

    def save_summaries(self, sha1: str, summaries: Dict[str, str]):
        """Save page summaries for a wiki version."""
        version_dir = self._get_version_dir(sha1)
        summaries_path = os.path.join(version_dir, "summaries.json")
        try:
            with open(summaries_path, 'w', encoding='utf-8') as f:
                json.dump(summaries, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Failed to save summaries: {e}")

    def get_summaries(self, sha1: str) -> Dict[str, str]:
        """Load page summaries for a wiki version. Uses class-level cache."""
        # Check cache first
        cache_key = f"{self.base_dir}:{sha1}"
        if cache_key in WikiVersionStore._summaries_cache:
            return WikiVersionStore._summaries_cache[cache_key].copy()

        version_dir = self._get_version_dir(sha1)
        summaries_path = os.path.join(version_dir, "summaries.json")
        summaries = {}
        if os.path.exists(summaries_path):
            try:
                with open(summaries_path, 'r', encoding='utf-8') as f:
                    summaries = json.load(f)
            except Exception as e:
                print(f"Failed to load summaries: {e}")

        # Store in cache
        with WikiVersionStore._cache_lock:
            WikiVersionStore._summaries_cache[cache_key] = summaries

        return summaries.copy()

    def save_chunks(self, sha1: str, chunks: List[Dict[str, Any]], embeddings=None):
        """Save indexed chunks for a wiki version."""
        version_dir = self._get_version_dir(sha1)

        # Save chunks (without tokens set - convert to list for JSON)
        chunks_data = []
        for chunk in chunks:
            chunks_data.append({
                "content": chunk["content"],
                "path": chunk["path"],
                "id": chunk["id"],
                "tokens": list(chunk.get("tokens", []))
            })

        with open(os.path.join(version_dir, "chunks.json"), 'w', encoding='utf-8') as f:
            json.dump(chunks_data, f, indent=2)

        # Save embeddings as numpy file
        if embeddings is not None and has_embeddings():
            try:
                if hasattr(embeddings, 'cpu'):
                    emb_array = embeddings.cpu().numpy()
                else:
                    emb_array = embeddings
                np.save(os.path.join(version_dir, "embeddings.npy"), emb_array)
            except Exception as e:
                print(f"Failed to save embeddings: {e}")

    def get_pages(self, sha1: str) -> Dict[str, str]:
        """Load pages for a specific wiki version. Uses class-level cache."""
        # Check cache first (thread-safe)
        cache_key = f"{self.base_dir}:{sha1}"
        if cache_key in WikiVersionStore._pages_cache:
            return WikiVersionStore._pages_cache[cache_key].copy()

        version_dir = self._get_version_dir(sha1)
        pages = {}

        # Read metadata to get paths
        metadata_path = os.path.join(version_dir, "metadata.json")
        if not os.path.exists(metadata_path):
            return pages

        with open(metadata_path, 'r', encoding='utf-8') as f:
            metadata = json.load(f)

        # Load each page
        for path in metadata.get("paths", []):
            safe_name = path.replace("/", "_").replace("\\", "_")
            if not safe_name.endswith(".md"):
                safe_name += ".md"

            file_path = os.path.join(version_dir, safe_name)
            if os.path.exists(file_path):
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    # Remove our header comments
                    lines = content.split('\n')
                    clean_lines = []
                    for line in lines:
                        if line.startswith('<!-- PATH:') or line.startswith('<!-- SHA1:'):
                            continue
                        clean_lines.append(line)
                    pages[path] = '\n'.join(clean_lines).strip()

        # Store in cache (thread-safe)
        with WikiVersionStore._cache_lock:
            WikiVersionStore._pages_cache[cache_key] = pages

        return pages.copy()

    def get_chunks(self, sha1: str) -> Tuple[List[Dict[str, Any]], Optional[Any]]:
        """Load chunks and embeddings for a specific wiki version. Uses class-level cache."""
        # Check cache first (thread-safe)
        cache_key = f"{self.base_dir}:{sha1}"
        if cache_key in WikiVersionStore._chunks_cache:
            cached = WikiVersionStore._chunks_cache[cache_key]
            # Return deep copy of chunks (they have mutable sets), embeddings can be shared
            return [dict(c) for c in cached[0]], cached[1]

        version_dir = self._get_version_dir(sha1)
        chunks = []
        embeddings = None

        # Load chunks
        chunks_path = os.path.join(version_dir, "chunks.json")
        if os.path.exists(chunks_path):
            with open(chunks_path, 'r', encoding='utf-8') as f:
                chunks_data = json.load(f)

            for chunk in chunks_data:
                chunks.append({
                    "content": chunk["content"],
                    "path": chunk["path"],
                    "id": chunk["id"],
                    "tokens": set(chunk.get("tokens", []))
                })

        # Load embeddings
        embeddings_path = os.path.join(version_dir, "embeddings.npy")
        if os.path.exists(embeddings_path) and has_embeddings():
            try:
                emb_array = np.load(embeddings_path)
                embeddings = torch.tensor(emb_array)
            except Exception as e:
                print(f"Failed to load embeddings: {e}")

        # Store in cache (thread-safe)
        with WikiVersionStore._cache_lock:
            WikiVersionStore._chunks_cache[cache_key] = (chunks, embeddings)

        return [dict(c) for c in chunks], embeddings

    def get_all_versions(self) -> List[Dict[str, Any]]:
        """Get list of all stored wiki versions."""
        versions = []
        for sha1, info in self.index.get("versions", {}).items():
            versions.append({
                "sha1": sha1,
                "created_at": info.get("created_at"),
                "is_current": sha1 == self.index.get("current")
            })
        return sorted(versions, key=lambda x: x.get("created_at", ""), reverse=True)

    def set_current(self, sha1: str):
        """Mark a version as current."""
        if sha1 in self.index["versions"]:
            self.index["current"] = sha1
            self._save_index()
