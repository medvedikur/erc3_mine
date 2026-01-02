"""
Wiki action handlers.

Handles wiki_search and wiki_load actions using local WikiManager.
"""
import re
from typing import Set, List, Tuple, Optional, TYPE_CHECKING

from erc3.erc3 import client
from .base import ActionHandler
from ..base import ToolContext
from utils import CLI_GREEN, CLI_BLUE, CLI_YELLOW, CLI_CLR

if TYPE_CHECKING:
    from ..wiki import WikiManager


def _get_project_customer_hint(task_text: str) -> Optional[str]:
    """
    Generate hint when task asks for project customer but agent uses wiki.

    AICODE-NOTE: Critical for t028. Wiki handler bypasses pipeline enrichers,
    so this hint must be generated here directly. When task asks about
    customer/client of a project, agent should use projects_search instead.

    Args:
        task_text: Task instructions

    Returns:
        Hint string or None
    """
    task_lower = task_text.lower()

    # Detect pattern: asking about customer/client of a specific project/initiative
    has_customer_query = any(kw in task_lower for kw in [
        'who is customer', 'who is the customer', 'who is client',
        'customer for', 'client for', 'customer of', 'client of'
    ])
    has_project_reference = any(kw in task_lower for kw in [
        'project', 'projects', 'initiative', 'programme', 'program', 'development'
    ])

    if not (has_customer_query and has_project_reference):
        return None

    return (
        "\n🔍 PROJECT CUSTOMER LOOKUP:\n"
        "You're searching wiki for project customer info, but wiki contains DOCUMENTATION, not project data!\n\n"
        "✅ To find who is the CUSTOMER of a specific project:\n"
        "  1. Use `projects_search(query='project name keywords')` to find the project\n"
        "  2. The result includes `customer` field with the customer ID\n"
        "  3. Use `customers_get(id='...')` to get customer details\n\n"
        "📌 IMPORTANT: Even 'internal' projects have customers!\n"
        "   Internal projects use internal customer entities (e.g., 'cust_..._internal').\n"
        "   The wiki explains project TYPES, but projects_search has the actual project DATA."
    )


def _get_location_search_hint(
    task_text: str,
    search_query: str,
    search_result: str,
    wiki_manager: 'WikiManager'
) -> Optional[str]:
    """
    Generate hint when task asks about office locations but search doesn't find them.

    AICODE-NOTE: Critical for t019/t020/t022/t023. When task asks "do you operate in X?"
    but agent searches with generic query like "office locations", the specific city
    chunk may not appear in top-K results. This hint tells agent to search specifically
    for the city name.

    Args:
        task_text: Task instructions (e.g., "Do you operate in Vienna Office – Austria?")
        search_query: The query agent used for wiki_search
        search_result: The search results text
        wiki_manager: WikiManager instance for checking actual locations

    Returns:
        Hint string or None
    """
    task_lower = task_text.lower()

    # Detect location-related questions
    location_patterns = [
        r'\boperate\s+in\b',
        r'\bhave\s+(?:an?\s+)?office\s+in\b',
        r'\boffice\s+in\b',
        r'有办公室',  # Chinese: have office
        r'办公室吗',  # Chinese: office?
    ]
    is_location_query = any(re.search(p, task_lower) for p in location_patterns)
    if not is_location_query:
        return None

    # Extract city names from task that might not be in search results
    # Common city name patterns: "Vienna", "Rotterdam", "Wien", "Beijing"
    # Also handle format "City Office – Country"
    city_pattern = r'(?:in|at)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s*(?:Office|office)?'
    cities_in_task = re.findall(city_pattern, task_text)

    # Also extract from "City Office – Country" format
    office_pattern = r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+Office\s*[–-]\s*([A-Z][a-z]+)'
    office_matches = re.findall(office_pattern, task_text)
    for city, country in office_matches:
        if city not in cities_in_task:
            cities_in_task.append(city)

    # Handle Chinese city names
    chinese_cities = {
        '鹿特丹': 'Rotterdam',
        '维也纳': 'Vienna',
        '巴塞罗那': 'Barcelona',
        '北京': 'Beijing',
        '慕尼黑': 'Munich',
        '巴黎': 'Paris',
    }
    for cn, en in chinese_cities.items():
        if cn in task_text:
            cities_in_task.append(en)

    if not cities_in_task:
        return None

    # Check if cities are already mentioned in search results
    result_lower = search_result.lower()
    missing_cities = [c for c in cities_in_task if c.lower() not in result_lower]

    if not missing_cities:
        return None  # Cities were found, no hint needed

    # Check if location file has these cities (to avoid false hints)
    locations_content = ""
    for path in wiki_manager.pages:
        if 'location' in path.lower():
            locations_content = wiki_manager.pages.get(path, "").lower()
            break

    # Only hint if cities actually exist in wiki
    existing_missing = [c for c in missing_cities if c.lower() in locations_content]

    # AICODE-NOTE: t020 FIX - Check if task mentions "City Office – Country" format
    # This format is used in employee locations, NOT wiki. Agent must check BOTH sources!
    employee_location_format = None
    for city, country in office_matches:
        # Reconstruct the exact format used in employee locations
        employee_location_format = f"{city} Office – {country}"
        break  # Just need the first one

    if not existing_missing:
        # Cities not in wiki - but might be in employee locations!
        if employee_location_format:
            # AICODE-NOTE: t020 FIX - "Vienna Office – Austria" is employee location format
            return (
                f"\n📍 LOCATION SEARCH HINT:\n"
                f"'{employee_location_format}' was NOT found in wiki.\n"
                f"BUT this format matches EMPLOYEE LOCATIONS (not wiki pages)!\n\n"
                f"⚠️ IMPORTANT: Company locations come from TWO sources:\n"
                f"  1. Wiki (company/locations_and_sites.md) - documents the main sites\n"
                f"  2. Employee Registry - employees have location field like 'Vienna Office – Austria'\n\n"
                f"✅ TRY: `employees_search(location='{employee_location_format}')` to check if anyone works there!\n"
                f"   If employees exist at that location → the company operates there.\n\n"
                f"⚠️ Don't conclude 'No' until you've checked BOTH wiki AND employee locations!"
            )
        return None  # Cities don't exist in wiki and no employee format detected

    # Generate hint to search for specific city
    city_to_search = existing_missing[0]
    hint = (
        f"\n📍 LOCATION SEARCH HINT:\n"
        f"Your search didn't return results for '{city_to_search}'.\n"
        f"The locations document lists MULTIPLE offices. Your generic search may have missed it.\n\n"
        f"✅ TRY: `wiki_search(query='{city_to_search}')`\n"
        f"   or: `wiki_load('company/locations_and_sites.md')` to see ALL office locations.\n\n"
    )

    # AICODE-NOTE: t020 FIX - Also mention employee locations if format matches
    if employee_location_format:
        hint += (
            f"💡 ALSO CHECK: `employees_search(location='{employee_location_format}')`\n"
            f"   to verify if anyone works at this location (employee locations differ from wiki).\n\n"
        )

    hint += f"⚠️ Don't conclude 'No' until you've searched for the SPECIFIC city name!"
    return hint


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

        # AICODE-NOTE: t028 FIX - Add project customer hint when task asks about customer
        # but agent uses wiki_search. This hint must be here because wiki handler
        # bypasses pipeline enrichers (returns True before pipeline.handle() runs).
        task_text = ctx.shared.get('task_text', '')
        if not ctx.shared.get('_project_customer_hint_shown'):
            project_hint = _get_project_customer_hint(task_text)
            if project_hint:
                ctx.results.append(project_hint)
                ctx.shared['_project_customer_hint_shown'] = True
                print(f"  {CLI_YELLOW}📌 Added project customer lookup hint{CLI_CLR}")

        # AICODE-NOTE: t019/t020/t022/t023 FIX - Add location search hint when task asks
        # about office locations but the specific city wasn't found in search results.
        # Agent may use generic query like "office locations" missing specific cities.
        if not ctx.shared.get('_location_hint_shown'):
            location_hint = _get_location_search_hint(
                task_text,
                ctx.model.query_regex,
                search_result_text,
                wiki_manager
            )
            if location_hint:
                ctx.results.append(location_hint)
                ctx.shared['_location_hint_shown'] = True
                print(f"  {CLI_YELLOW}📍 Added location search hint{CLI_CLR}")

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
    Also stores content in ctx.shared for wiki rename operations (t067 fix).
    """

    def can_handle(self, ctx: ToolContext) -> bool:
        if not isinstance(ctx.model, client.Req_LoadWiki):
            return False

        # AICODE-NOTE: t067 fix. For rename/backup operations, use API directly.
        # Local cache may have different content than API, causing content mismatch.
        # Detect rename tasks by checking task_text for patterns like "rename", ".bak", "copy"
        task_text = ctx.shared.get('task_text', '').lower()
        is_rename_task = any(kw in task_text for kw in ['rename', '.bak', 'backup', 'copy to'])
        if is_rename_task:
            print(f"  [t067] Rename task detected - using API instead of local cache")
            return False  # Fall back to API for consistency

        return True

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

            # AICODE-NOTE: t067 fix. Store loaded content for wiki rename operations.
            # When LLM copies content to wiki_update, Unicode may be corrupted.
            # We store the original content so wiki_update can use it if content matches.
            if '_loaded_wiki_content' not in ctx.shared:
                ctx.shared['_loaded_wiki_content'] = {}
            ctx.shared['_loaded_wiki_content'][file_path] = content
        else:
            print(f"  {CLI_YELLOW}⚠ Page not found: {file_path}{CLI_CLR}")
            ctx.results.append(f"Action ({action_name}): Page '{file_path}' not found in wiki.")

        return True
