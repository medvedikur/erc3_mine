import re
import os
import json
from typing import Dict, List, Optional, Tuple, Any
from erc3.erc3 import client
from .base import ToolContext, Middleware

# Console color codes
CLI_YELLOW = "\x1B[33m"
CLI_CLR = "\x1B[0m"

# Try importing sentence_transformers for local embeddings
try:
    from sentence_transformers import SentenceTransformer, util
    import torch
    import numpy as np
    HAS_EMBEDDINGS = True
except ImportError:
    HAS_EMBEDDINGS = False
    print("⚠️ sentence-transformers not found. Falling back to keyword search.")

# Storage paths
WIKI_DUMP_DIR = "wiki_dump"
VERSIONS_INDEX = os.path.join(WIKI_DUMP_DIR, "versions.json")


class WikiVersionStore:
    """
    File-based storage for wiki versions.
    Each version stored in wiki_dump/{sha1_prefix}/ folder.
    """
    def __init__(self, base_dir: str = WIKI_DUMP_DIR):
        self.base_dir = base_dir
        os.makedirs(base_dir, exist_ok=True)
        self._load_index()
    
    def _load_index(self):
        """Load versions index from JSON file."""
        if os.path.exists(VERSIONS_INDEX):
            try:
                with open(VERSIONS_INDEX, 'r', encoding='utf-8') as f:
                    self.index = json.load(f)
            except Exception as e:
                print(f"⚠️ Failed to load wiki index: {e}")
                self.index = {"versions": {}, "current": None}
        else:
            self.index = {"versions": {}, "current": None}
    
    def _save_index(self):
        """Save versions index to JSON file."""
        try:
            with open(VERSIONS_INDEX, 'w', encoding='utf-8') as f:
                json.dump(self.index, f, indent=2)
        except Exception as e:
            print(f"⚠️ Failed to save wiki index: {e}")
    
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
            "created_at": __import__('datetime').datetime.now().isoformat()
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
        if embeddings is not None and HAS_EMBEDDINGS:
            try:
                if hasattr(embeddings, 'cpu'):
                    emb_array = embeddings.cpu().numpy()
                else:
                    emb_array = embeddings
                np.save(os.path.join(version_dir, "embeddings.npy"), emb_array)
            except Exception as e:
                print(f"⚠️ Failed to save embeddings: {e}")
    
    def get_pages(self, sha1: str) -> Dict[str, str]:
        """Load pages for a specific wiki version."""
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
        
        return pages
    
    def get_chunks(self, sha1: str) -> Tuple[List[Dict[str, Any]], Optional[Any]]:
        """Load chunks and embeddings for a specific wiki version."""
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
        if os.path.exists(embeddings_path) and HAS_EMBEDDINGS:
            try:
                emb_array = np.load(embeddings_path)
                embeddings = torch.tensor(emb_array)
            except Exception as e:
                print(f"⚠️ Failed to load embeddings: {e}")
        
        return chunks, embeddings
    
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


class WikiManager:
    """
    Manages the local cache of the company wiki with version history.
    All versions are stored in wiki_dump/{sha1}/ folders.
    
    Implements Hybrid RAG Search combining three streams:
    1. REGEX: Pattern matching for structured queries
    2. SEMANTIC: Vector similarity using sentence-transformers (if available)
    3. KEYWORD: Token overlap fallback for broad matching
    """
    def __init__(self, api: Optional[client.Erc3Client] = None):
        self.api = api
        self.current_sha1: str = ""
        self.pages: Dict[str, str] = {}
        self.chunks: List[Dict[str, Any]] = []
        self.corpus_embeddings = None
        
        # Track wiki changes for dynamic injection
        self._last_synced_sha1: Optional[str] = None
        self._sha1_change_count: int = 0
        
        # Initialize version store
        self.store = WikiVersionStore()
        
        # Initialize embedding model
        self.model = None
        if HAS_EMBEDDINGS:
            try:
                model_name = 'all-MiniLM-L6-v2'
                print(f"🧠 Initializing Local Embedding Model ({model_name})...")
                self.model = SentenceTransformer(model_name)
            except Exception as e:
                print(f"⚠️ Failed to load embedding model: {e}")
                self.model = None

    def set_api(self, api: client.Erc3Client):
        self.api = api

    def sync(self, reported_sha1: str) -> bool:
        """
        Check if sync is needed and update if so.
        
        Returns:
            True if wiki hash CHANGED (agent should re-read critical docs)
            False if wiki was already at this version
        """
        if not reported_sha1:
            return False

        if self.current_sha1 != reported_sha1:
            print(f"📚 Wiki Sync: Hash changed ({self.current_sha1[:8] if self.current_sha1 else 'none'} -> {reported_sha1[:8]})")
            
            # Track wiki changes for debugging
            if self._last_synced_sha1 and self._last_synced_sha1 != reported_sha1:
                self._sha1_change_count += 1
                print(f"  {CLI_YELLOW}⚠️ Wiki changed! ({self._sha1_change_count} times this session){CLI_CLR}")
            
            self._last_synced_sha1 = reported_sha1
            
            # Check if we already have this version cached
            if self.store.version_exists(reported_sha1):
                print(f"   ✓ Version found in local cache. Loading...")
                self._load_from_cache(reported_sha1)
            else:
                print(f"   ↓ New version. Downloading from API...")
                self._download_and_save(reported_sha1)
            
            old_sha1 = self.current_sha1
            self.current_sha1 = reported_sha1
            
            # Return True only if we had a previous version (not first load)
            return old_sha1 is not None
        
        return False

    def _load_from_cache(self, sha1: str):
        """Load a wiki version from local cache."""
        self.pages = self.store.get_pages(sha1)
        self.chunks, self.corpus_embeddings = self.store.get_chunks(sha1)
        self.store.set_current(sha1)
        print(f"📚 Wiki loaded from cache: {len(self.pages)} pages, {len(self.chunks)} chunks")

    def _download_and_save(self, sha1: str):
        """Download wiki from API and save to local cache."""
        if not self.api:
            print("⚠️ Wiki Sync Failed: No API client set.")
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
            
            # 4. Index/Chunk pages
            self._reindex()
            
            # 5. Save chunks to cache
            self.store.save_chunks(actual_sha1, self.chunks, self.corpus_embeddings)
            
            self.current_sha1 = actual_sha1
            print(f"📚 Wiki Sync Complete: {len(self.pages)} pages saved to wiki_dump/{actual_sha1[:16]}/")
            
        except Exception as e:
            print(f"⚠️ Wiki Sync Failed: {e}")
            import traceback
            traceback.print_exc()

    def _reindex(self):
        """Split pages into chunks for search and compute embeddings"""
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
            print(f"🧠 Computing embeddings for {len(self.chunks)} chunks...")
            texts = [c["content"] for c in self.chunks]
            try:
                self.corpus_embeddings = self.model.encode(texts, convert_to_tensor=True, show_progress_bar=False)
            except Exception as e:
                print(f"⚠️ Embedding computation failed: {e}")
                self.corpus_embeddings = None

    def search(self, query: str, top_k: int = 5, sha1: Optional[str] = None) -> str:
        """
        Hybrid Search: Regex + Semantic + Keyword.
        
        Args:
            query: Search query
            top_k: Number of results
            sha1: Optional wiki version to search (defaults to current)
        """
        # Load specific version if requested
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

        results = {}
        
        # ========== STREAM 1: Regex Search ==========
        regex_operators = r'.*+?[](){}|^$\\'
        has_regex_syntax = any(c in query for c in regex_operators)
        
        if has_regex_syntax:
            try:
                pattern = re.compile(query, re.IGNORECASE)
                for chunk in chunks:
                    if pattern.search(chunk["content"]):
                        chunk_id = chunk["id"]
                        if chunk_id not in results or results[chunk_id][0] < 0.95:
                            results[chunk_id] = (0.95, chunk, "regex")
            except re.error:
                pass
        
        # ========== STREAM 2: Semantic Search ==========
        if self.model is not None and embeddings is not None:
            clean_query = re.sub(r'[.*+?\[\](){}|^$\\]', ' ', query)
            clean_query = ' '.join(clean_query.split())
            
            if clean_query and len(clean_query) >= 3:
                try:
                    query_emb = self.model.encode(clean_query, convert_to_tensor=True)
                    hits = util.semantic_search(query_emb, embeddings, top_k=top_k * 2)
                    
                    for hit in hits[0]:
                        idx = hit['corpus_id']
                        chunk = chunks[idx]
                        chunk_id = chunk["id"]
                        score = hit['score']
                        
                        if score > 0.25:
                            if chunk_id not in results or results[chunk_id][0] < score:
                                results[chunk_id] = (score, chunk, "semantic")
                except Exception:
                    pass
        
        # ========== STREAM 3: Keyword Fallback ==========
        query_tokens = set(re.findall(r'\w+', query.lower()))
        if query_tokens:
            for chunk in chunks:
                overlap = len(query_tokens.intersection(chunk["tokens"]))
                if overlap > 0:
                    normalized_score = (overlap / len(query_tokens)) * 0.6
                    chunk_id = chunk["id"]
                    if chunk_id not in results:
                        results[chunk_id] = (normalized_score, chunk, "keyword")
        
        # ========== Merge & Rank ==========
        if not results:
            return f"No matches found for '{query}' in wiki."
        
        sorted_results = sorted(results.values(), key=lambda x: x[0], reverse=True)[:top_k]
        
        output = []
        for score, chunk, source in sorted_results:
            preview = chunk["content"][:500] + "..." if len(chunk["content"]) > 500 else chunk["content"]
            source_icon = {"regex": "🔎", "semantic": "🧠", "keyword": "📝"}.get(source, "")
            output.append(f"--- Document: {chunk['path']} (Score: {score:.4f} {source_icon}) ---\n{preview}\n")
        
        return "\n".join(output)

    def get_context_summary(self) -> str:
        """Return a summary for system prompt."""
        if not self.pages:
            return "Wiki not loaded yet."
        
        modes = ["Regex"]
        if self.model and self.corpus_embeddings is not None:
            modes.append("Semantic")
        modes.append("Keyword")
        
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
        Return content of critical policy documents that should be read after wiki changes.
        These are dynamically loaded from the wiki, NOT hardcoded rules.
        """
        critical_paths = [
            "rulebook.md",      # Always contains core policies
            "merger.md",        # May contain post-M&A rules (if exists)
            "hierarchy.md",     # Org structure and permissions
        ]
        
        docs = []
        for path in critical_paths:
            if path in self.pages:
                content = self.pages[path]
                # Truncate very long documents
                if len(content) > 2000:
                    content = content[:2000] + "\n... [truncated, use wiki_load for full content]"
                docs.append(f"=== {path} ===\n{content}")
        
        if not docs:
            return ""
        
        return "\n\n".join(docs)
    
    def has_page(self, path: str) -> bool:
        """Check if a wiki page exists."""
        return path in self.pages
    
    def get_page(self, path: str) -> Optional[str]:
        """Get a wiki page content directly."""
        return self.pages.get(path)


class WikiMiddleware(Middleware):
    """
    Middleware that syncs wiki when hash changes.
    """
    def __init__(self, manager: WikiManager):
        self.manager = manager

    def process(self, ctx: ToolContext) -> None:
        ctx.shared['wiki_manager'] = self.manager
