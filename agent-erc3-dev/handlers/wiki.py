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
    
    Implements Hybrid RAG Search combining three streams:
    1. REGEX: Pattern matching for structured queries (e.g. "salary|privacy|expense")
    2. SEMANTIC: Vector similarity using sentence-transformers (if available)
    3. KEYWORD: Token overlap fallback for broad matching
    
    Results from all streams are merged, deduplicated, and ranked by score.
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

    def search(self, query: str, top_k: int = 5) -> str:
        """
        Hybrid Search: combines Regex matching, Semantic Search, and Keyword fallback.
        Results are merged, deduplicated, and ranked by combined score.
        
        Strategy:
        1. REGEX STREAM: If query contains regex operators, perform pattern matching
        2. SEMANTIC STREAM: Clean query and perform vector similarity search
        3. KEYWORD STREAM: Fallback token overlap if other methods fail
        """
        if not self.chunks:
            return "Wiki not loaded yet or empty."

        results = {}  # chunk_id -> (score, chunk, source) for deduplication
        
        # ========== STREAM 1: Regex Search ==========
        regex_operators = r'.*+?[](){}|^$\\'
        has_regex_syntax = any(c in query for c in regex_operators)
        
        if has_regex_syntax:
            try:
                pattern = re.compile(query, re.IGNORECASE)
                regex_matches = 0
                for chunk in self.chunks:
                    if pattern.search(chunk["content"]):
                        chunk_id = chunk["id"]
                        # Regex exact match gets high priority score
                        if chunk_id not in results or results[chunk_id][0] < 0.95:
                            results[chunk_id] = (0.95, chunk, "regex")
                            regex_matches += 1
                if regex_matches > 0:
                    print(f"  🔎 Regex stream: {regex_matches} matches")
            except re.error as e:
                print(f"  ⚠️ Invalid regex pattern: {e}")
        
        # ========== STREAM 2: Semantic Search ==========
        if self.model is not None and self.corpus_embeddings is not None:
            # Clean query for semantic search: remove regex operators, normalize whitespace
            clean_query = re.sub(r'[.*+?\[\](){}|^$\\]', ' ', query)
            clean_query = ' '.join(clean_query.split())
            
            if clean_query and len(clean_query) >= 3:
                try:
                    query_emb = self.model.encode(clean_query, convert_to_tensor=True)
                    hits = util.semantic_search(query_emb, self.corpus_embeddings, top_k=top_k * 2)
                    
                    semantic_added = 0
                    for hit in hits[0]:
                        idx = hit['corpus_id']
                        chunk = self.chunks[idx]
                        chunk_id = chunk["id"]
                        score = hit['score']
                        
                        # Only add if score is reasonable and better than existing
                        if score > 0.25:
                            if chunk_id not in results or results[chunk_id][0] < score:
                                results[chunk_id] = (score, chunk, "semantic")
                                semantic_added += 1
                    
                    if semantic_added > 0:
                        print(f"  🧠 Semantic stream: {semantic_added} matches (query: '{clean_query}')")
                        
                except Exception as e:
                    print(f"  ⚠️ Semantic search error: {e}")
        
        # ========== STREAM 3: Keyword Fallback ==========
        # Always run keyword search as additional signal
        query_tokens = set(re.findall(r'\w+', query.lower()))
        if query_tokens:
            keyword_added = 0
            for chunk in self.chunks:
                overlap = len(query_tokens.intersection(chunk["tokens"]))
                if overlap > 0:
                    # Normalize keyword score to 0-1 range, but lower weight
                    normalized_score = (overlap / len(query_tokens)) * 0.6 if query_tokens else 0
                    chunk_id = chunk["id"]
                    
                    # Only add if not already found by better method
                    if chunk_id not in results:
                        results[chunk_id] = (normalized_score, chunk, "keyword")
                        keyword_added += 1
            
            if keyword_added > 0 and not results:
                print(f"  📝 Keyword stream: {keyword_added} matches")
        
        # ========== Merge & Rank ==========
        if not results:
            return f"No matches found for '{query}' in local wiki cache."
        
        # Sort by score descending and take top_k
        sorted_results = sorted(results.values(), key=lambda x: x[0], reverse=True)[:top_k]
        
        output = []
        for score, chunk, source in sorted_results:
            preview = chunk["content"][:500] + "..." if len(chunk["content"]) > 500 else chunk["content"]
            source_icon = {"regex": "🔎", "semantic": "🧠", "keyword": "📝"}.get(source, "")
            output.append(f"--- Document: {chunk['path']} (Score: {score:.4f} {source_icon}) ---\n{preview}\n")
        
        return "\n".join(output)

    def get_context_summary(self) -> str:
        """Return a summary of available wiki pages for the system prompt"""
        if not self.pages:
            return "Wiki not loaded yet."
        
        # Determine active search modes
        modes = []
        modes.append("Regex")
        if self.model and self.corpus_embeddings is not None:
            modes.append("Semantic")
        modes.append("Keyword")
        
        mode_str = "Hybrid: " + " + ".join(modes)
        return f"Available Wiki Pages: {', '.join(self.pages.keys())} (Mode: {mode_str})"


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
