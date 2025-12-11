"""
Wiki action handlers.

Handles wiki_search and wiki_load actions using local WikiManager.
"""
import re
from typing import Set, List, Tuple, TYPE_CHECKING

from erc3.erc3 import client
from .base import ActionHandler
from ..base import ToolContext
from utils import CLI_GREEN, CLI_BLUE, CLI_YELLOW, CLI_CLR

if TYPE_CHECKING:
    from ..wiki import WikiManager


# Stopwords for filtering query keywords when matching wiki filenames
_STOPWORDS: Set[str] = {
    'the', 'a', 'an', 'is', 'are', 'what', 'how', 'to', 'for', 'of', 'in',
    'and', 'or', 'with', 'after', 'new', 'i', 'my', 'me', 'do', 'can',
    'please', 'need', 'want', 'about', 'on', 'be', 'have', 'has', 'get',
    'requirements', 'time', 'tracking'
}


class WikiSearchHandler(ActionHandler):
    """
    Handles wiki_search action using local WikiManager with Smart RAG.

    Features:
    - Semantic search via embeddings
    - Filename match hints for relevant files not in top results
    """

    def can_handle(self, ctx: ToolContext) -> bool:
        return isinstance(ctx.model, client.Req_SearchWiki)

    def handle(self, ctx: ToolContext) -> bool:
        wiki_manager = ctx.shared.get('wiki_manager')
        if not wiki_manager:
            return False  # Fall back to API

        action_name = ctx.model.__class__.__name__
        print(f"  {CLI_BLUE}🔍 Using Local Wiki Search (Smart RAG){CLI_CLR}")

        # Execute search
        search_result_text = wiki_manager.search(ctx.model.query_regex)

        # Add filename match hints
        filename_hint = self._get_filename_hints(
            ctx.model.query_regex,
            wiki_manager,
            search_result_text
        )
        if filename_hint:
            search_result_text += filename_hint

        print(f"  {CLI_GREEN}✓ SUCCESS (Local){CLI_CLR}")
        ctx.results.append(f"Action ({action_name}): SUCCESS\nResult: {search_result_text}")
        return True

    def _get_filename_hints(
        self,
        query: str,
        wiki_manager: 'WikiManager',
        search_result_text: str
    ) -> str:
        """
        Generate hints for wiki files that match query keywords but weren't in search results.

        Args:
            query: Search query
            wiki_manager: WikiManager instance
            search_result_text: Current search results text

        Returns:
            Hint string or empty string
        """
        query_lower = query.lower()
        query_words = set(re.findall(r'\w+', query_lower)) - _STOPWORDS

        if not query_words or not wiki_manager.pages:
            return ""

        matching_files: List[Tuple[str, Set[str]]] = []
        for wiki_path in wiki_manager.pages.keys():
            # Extract filename without extension
            filename = wiki_path.replace('.md', '').replace('_', ' ').lower()
            filename_words = set(re.findall(r'\w+', filename))

            # Check for significant word overlap
            overlap = query_words & filename_words
            if overlap and len(overlap) >= 1:
                # Check if this file is already in search results
                if wiki_path not in search_result_text:
                    matching_files.append((wiki_path, overlap))

        if not matching_files:
            return ""

        hint_lines = []
        for path, overlap in matching_files[:3]:  # Max 3 suggestions
            hint_lines.append(f"  - `{path}` (matches: {', '.join(overlap)})")

        print(f"  {CLI_YELLOW}📝 Added filename match hint: {[f[0] for f in matching_files[:3]]}{CLI_CLR}")

        return (
            f"\n💡 FILENAME MATCH: These wiki files match your query keywords but weren't in top search results:\n"
            + "\n".join(hint_lines)
            + f"\nConsider loading them with `wiki_load(\"{matching_files[0][0]}\")` for more relevant info."
        )


class WikiLoadHandler(ActionHandler):
    """
    Handles wiki_load action using local WikiManager.

    Loads specific wiki page content from local cache.
    """

    def can_handle(self, ctx: ToolContext) -> bool:
        return isinstance(ctx.model, client.Req_LoadWiki)

    def handle(self, ctx: ToolContext) -> bool:
        wiki_manager = ctx.shared.get('wiki_manager')
        if not wiki_manager:
            return False  # Fall back to API

        action_name = ctx.model.__class__.__name__
        file_path = ctx.model.file
        print(f"  {CLI_BLUE}📄 Using Local Wiki Load: {file_path}{CLI_CLR}")

        if wiki_manager.has_page(file_path):
            content = wiki_manager.get_page(file_path)
            print(f"  {CLI_GREEN}✓ SUCCESS (Local){CLI_CLR}")
            ctx.results.append(f"Action ({action_name}): SUCCESS\nFile: {file_path}\nContent:\n{content}")
        else:
            print(f"  {CLI_YELLOW}⚠ Page not found: {file_path}{CLI_CLR}")
            ctx.results.append(f"Action ({action_name}): Page '{file_path}' not found in wiki.")

        return True
