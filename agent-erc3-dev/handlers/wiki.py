import re
from typing import Dict, List, Optional, Tuple
from erc3.erc3 import client
from .base import ToolContext, Middleware

class WikiManager:
    """
    Manages the local cache of the company wiki.
    Syncs with the API when sha1 changes.
    Implements simple RAG-like search.
    """
    def __init__(self, api: Optional[client.Erc3Client] = None):
        self.api = api
        self.current_sha1: str = ""
        self.pages: Dict[str, str] = {} # path -> content
        self.chunks: List[Dict[str, Any]] = [] # list of {content, path, id}

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
                for path in list_resp.paths:
                    print(f"   Downloading {path}...")
                    load_resp = self.api.load_wiki(path)
                    self.pages[path] = load_resp.content
                
                # 3. Index/Chunk pages
                self._reindex()
                    
                print(f"📚 Wiki Sync Complete: {len(self.pages)} pages loaded.")
                
            except Exception as e:
                print(f"⚠️ Wiki Sync Failed: {e}")

    def _reindex(self):
        """Split pages into chunks for search"""
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
                    "tokens": set(re.findall(r'\w+', clean_p.lower())) # Pre-tokenize for Jaccard/BM25
                })

    def search(self, query: str) -> str:
        """
        Smart keyword search across chunks.
        Uses token overlap ranking.
        """
        if not self.chunks:
            return "Wiki not loaded yet or empty."

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
            # Truncate content if too long for preview
            preview = chunk["content"][:500] + "..." if len(chunk["content"]) > 500 else chunk["content"]
            output.append(f"--- Document: {chunk['path']} (Relevance: {score}) ---\n{preview}\n")
            
        return "\n".join(output)

    def get_context_summary(self) -> str:
        """Return a summary of available wiki pages for the system prompt"""
        if not self.pages:
            return "Wiki not loaded yet."
        return "Available Wiki Pages: " + ", ".join(self.pages.keys())


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
