import re
from typing import Dict, List, Optional
from erc3.erc3 import client
from .base import ToolContext, Middleware

class WikiManager:
    """
    Manages the local cache of the company wiki.
    Syncs with the API when sha1 changes.
    """
    def __init__(self, api: client.Erc3Client):
        self.api = api
        self.current_sha1: str = ""
        self.pages: Dict[str, str] = {} # path -> content

    def sync(self, reported_sha1: str):
        """Check if sync is needed and update if so."""
        if not reported_sha1:
            return

        if self.current_sha1 != reported_sha1:
            print(f"📚 Wiki Sync: Hash changed ({self.current_sha1[:8]} -> {reported_sha1[:8]}). Updating...")
            try:
                # 1. List all pages
                list_resp = self.api.list_wiki()
                self.current_sha1 = list_resp.sha1
                
                # 2. Load all pages (for a small wiki this is fine)
                # If wiki is huge, we might want to lazy load, but for agents having full context is better.
                for path in list_resp.paths:
                    print(f"   Downloading {path}...")
                    load_resp = self.api.load_wiki(path)
                    self.pages[path] = load_resp.content
                    
                print(f"📚 Wiki Sync Complete: {len(self.pages)} pages loaded.")
                
            except Exception as e:
                print(f"⚠️ Wiki Sync Failed: {e}")

    def search(self, query_regex: str) -> str:
        """Local regex search across cached pages"""
        results = []
        try:
            pattern = re.compile(query_regex, re.IGNORECASE)
            for path, content in self.pages.items():
                for i, line in enumerate(content.split('\n')):
                    if pattern.search(line):
                        results.append(f"[{path}:{i+1}] {line.strip()}")
        except re.error as e:
            return f"Invalid Regex: {e}"
        
        if not results:
            return "No matches found in local wiki cache."
        
        return "\n".join(results[:20]) # Limit results

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
        # We can't easily intercept the *response* here to get the hash 
        # because middleware runs *before* execution in this pattern.
        # However, we can enforce a check if the agent *explicitly* asks for wiki stuff.
        
        # ACTUALLY: The best way is to let the DefaultActionHandler execute, 
        # and then inspect the result? Or pass the manager to the handler?
        
        # For now, let's just use this to inject the WikiManager into the context 
        # so custom handlers can use it.
        ctx.shared['wiki_manager'] = self.manager

