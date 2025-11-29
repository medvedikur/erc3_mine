import re
from typing import Dict, List, Optional, Tuple, Any
from erc3.erc3 import client
from .base import ToolContext, Middleware

# Try importing sentence_transformers for local embeddings
try:
    from sentence_transformers import SentenceTransformer, util
    import torch
    HAS_EMBEDDINGS = True
except ImportError:
    HAS_EMBEDDINGS = False
    print("⚠️ sentence-transformers not found. Falling back to keyword search.")

class WikiManager:
    """
    Manages the local cache of the company wiki.
    Syncs with the API when sha1 changes.
    Implements simple RAG-like search using local embeddings (if available) or keyword overlap.
    """
    def __init__(self, api: Optional[client.Erc3Client] = None):
        self.api = api
        self.current_sha1: str = ""
        self.pages: Dict[str, str] = {} # path -> content
        self.chunks: List[Dict[str, Any]] = [] # list of {content, path, id}
        self.corpus_embeddings = None
        
        self.model = None
        if HAS_EMBEDDINGS:
            try:
                # Use a small, fast model suitable for local inference
                model_name = 'all-MiniLM-L6-v2'
                print(f"🧠 Initializing Local Embedding Model ({model_name})...")
                self.model = SentenceTransformer(model_name)
            except Exception as e:
                print(f"⚠️ Failed to load embedding model: {e}")
                self.model = None

    def set_api(self, api: client.Erc3Client):
        self.api = api

    def sync(self, reported_sha1: str):
        """Check if sync is needed and update if so."""
        if not reported_sha1:
            return

        if self.current_sha1 != reported_sha1:
            print(f"📚 Wiki Sync: Hash changed ({self.current_sha1[:8]} -> {reported_sha1[:8]}). Updating...")
            if not self.api:
                print("⚠️ Wiki Sync Failed: No API client set.")
                return

            try:
                # 1. List all pages
                list_resp = self.api.list_wiki()
                self.current_sha1 = list_resp.sha1
                
                # 2. Load all pages
                self.pages = {}
                import os
                # Ensure dump directory exists relative to CWD
                dump_dir = "wiki_dump"
                if not os.path.exists(dump_dir):
                    # Try creating it, fallback to just "wiki_dump" if running from subdir
                    try:
                        os.makedirs(dump_dir, exist_ok=True)
                    except OSError:
                        dump_dir = "wiki_dump"
                        os.makedirs(dump_dir, exist_ok=True)
                
                for path in list_resp.paths:
                    print(f"   Downloading {path}...")
                    load_resp = self.api.load_wiki(path)
                    self.pages[path] = load_resp.content
                    
                    # DEBUG: Dump raw content to file
                    try:
                        safe_name = path.replace("/", "_").replace("\\", "_")
                        if not safe_name.endswith(".md"): safe_name += ".md"
                        with open(f"{dump_dir}/{safe_name}", "w", encoding="utf-8") as f:
                            f.write(f"--- PATH: {path} ---\n\n")
                            f.write(load_resp.content)
                    except Exception as e:
                        print(f"⚠️ Failed to dump wiki page {path}: {e}")
                
                # 3. Index/Chunk pages
                self._reindex()
                    
                print(f"📚 Wiki Sync Complete: {len(self.pages)} pages loaded.")
                
            except Exception as e:
                print(f"⚠️ Wiki Sync Failed: {e}")

    def _reindex(self):
        """Split pages into chunks for search and compute embeddings"""
        self.chunks = []
        for path, content in self.pages.items():
            # Simple splitting by double newline (paragraphs)
            paragraphs = content.split('\n\n')
            for i, p in enumerate(paragraphs):
                clean_p = p.strip()
                if not clean_p: 
                    continue
                # Further split long paragraphs if needed? For now keep it simple.
                self.chunks.append({
                    "content": clean_p,
                    "path": path,
                    "id": f"{path}#{i}",
                    "tokens": set(re.findall(r'\w+', clean_p.lower())) # Pre-tokenize for Jaccard fallback
                })
        
        # Compute embeddings if model is available
        if self.model and self.chunks:
            print(f"🧠 Computing embeddings for {len(self.chunks)} chunks...")
            texts = [c["content"] for c in self.chunks]
            try:
                self.corpus_embeddings = self.model.encode(texts, convert_to_tensor=True, show_progress_bar=True)
            except Exception as e:
                print(f"⚠️ Embedding computation failed: {e}")
                self.corpus_embeddings = None

    def search(self, query: str) -> str:
        """
        Smart search across chunks.
        Uses Semantic Search (Cosine Similarity) if model loaded,
        otherwise falls back to token overlap.
        """
        if not self.chunks:
            return "Wiki not loaded yet or empty."

        # 1. Try Vector Search
        if self.model is not None and self.corpus_embeddings is not None:
            try:
                query_emb = self.model.encode(query, convert_to_tensor=True)
                
                # Semantic Search
                hits = util.semantic_search(query_emb, self.corpus_embeddings, top_k=5)
                top_hits = hits[0] # We only have one query
                
                output = []
                for hit in top_hits:
                    score = hit['score']
                    idx = hit['corpus_id']
                    chunk = self.chunks[idx]
                    
                    # Truncate content if too long for preview
                    preview = chunk["content"][:500] + "..." if len(chunk["content"]) > 500 else chunk["content"]
                    output.append(f"--- Document: {chunk['path']} (Relevance: {score:.4f}) ---\n{preview}\n")
                
                if not output:
                     return f"No semantic matches found for '{query}'."

                return "\n".join(output)
            
            except Exception as e:
                print(f"⚠️ Vector search error: {e}. Falling back to keyword search.")

        # 2. Fallback: Token Overlap
        query_tokens = set(re.findall(r'\w+', query.lower()))
        if not query_tokens:
            return "Empty search query."

        results = []
        for chunk in self.chunks:
            # Score: overlap count
            overlap = len(query_tokens.intersection(chunk["tokens"]))
            if overlap > 0:
                results.append((overlap, chunk))
        
        # Sort by overlap desc
        results.sort(key=lambda x: x[0], reverse=True)
        
        # Top 5 chunks
        top_results = results[:5]
        
        if not top_results:
             return f"No matches found for '{query}' in local wiki cache."

        output = []
        for score, chunk in top_results:
            preview = chunk["content"][:500] + "..." if len(chunk["content"]) > 500 else chunk["content"]
            output.append(f"--- Document: {chunk['path']} (Relevance: {score}) ---\n{preview}\n")
            
        return "\n".join(output)

    def get_context_summary(self) -> str:
        """Return a summary of available wiki pages for the system prompt"""
        if not self.pages:
            return "Wiki not loaded yet."
        mode = "Semantic Search" if (self.model and self.corpus_embeddings is not None) else "Keyword Search"
        return f"Available Wiki Pages: {', '.join(self.pages.keys())} (Mode: {mode})"


class WikiMiddleware(Middleware):
    """
    Middleware that checks if an action reveals a new wiki hash 
    (e.g. who_am_i) and triggers a sync on the shared WikiManager.
    """
    def __init__(self, manager: WikiManager):
        self.manager = manager

    def process(self, ctx: ToolContext) -> None:
        # Inject manager into shared context so handlers can use it
        ctx.shared['wiki_manager'] = self.manager
