"""
Wiki hint enrichers.

Provides hints about relevant wiki files based on task keywords.
"""
import re
from typing import Any, Dict, List, Optional, Set, Tuple, TYPE_CHECKING

from utils import CLI_YELLOW, CLI_CLR

if TYPE_CHECKING:
    from ..wiki import WikiManager


# Stopwords for filtering task keywords
_TASK_STOPWORDS: Set[str] = {
    'the', 'a', 'an', 'is', 'are', 'what', 'how', 'to', 'for', 'of', 'in',
    'and', 'or', 'with', 'after', 'new', 'i', 'my', 'me', 'do', 'can',
    'please', 'need', 'want', 'about', 'on', 'be', 'have', 'has', 'get',
    'requirements', 'time', 'tracking'
}

# Patterns that indicate self-mutation tasks (don't need wiki hints)
_SELF_MUTATION_PATTERNS = [
    r'\b(add|update|change|set)\b.{0,20}\bmy\s+(skills?|location|department|notes?)\b',
    r'\bmy\s+(skills?|location|department)\b.{0,20}\b(add|update|change|set)\b',
]

# Files already included in critical docs (skip in hints)
_CRITICAL_PATHS = {'rulebook.md', 'merger.md', 'hierarchy.md'}


class WikiHintEnricher:
    """
    Generates hints about wiki files relevant to the current task.

    Matches task keywords against wiki filenames to suggest relevant
    documentation the agent should consult.
    """

    def get_task_file_hints(
        self,
        wiki_manager: 'WikiManager',
        task_text: str,
        is_public_user: bool = False,
        skip_critical: bool = True,
        context: str = "wiki_change"
    ) -> Optional[str]:
        """
        Generate hints about wiki files matching task keywords.

        Args:
            wiki_manager: WikiManager instance with loaded pages
            task_text: Task text to extract keywords from
            is_public_user: If True, skip hints (limited access)
            skip_critical: If True, skip files in _CRITICAL_PATHS
            context: Context for logging ("wiki_change" or "wiki_list")

        Returns:
            Hint string or None if no relevant files found
        """
        if not task_text or not wiki_manager or not wiki_manager.pages:
            return None

        # Skip for public users
        if is_public_user:
            return None

        task_lower = task_text.lower()

        # Skip for self-mutation tasks
        if self._is_self_mutation(task_lower):
            return None

        # Extract keywords
        task_words = self._extract_keywords(task_lower)
        if not task_words:
            return None

        # Find matching files
        matching_files = self._find_matching_files(
            wiki_manager.pages.keys(),
            task_words,
            skip_critical
        )

        if not matching_files:
            return None

        # Format hint
        hint = self._format_hint(matching_files, context)
        print(f"  {CLI_YELLOW}📝 Task-specific file hint ({context}): {[f[0] for f in matching_files[:3]]}{CLI_CLR}")
        return hint

    def _is_self_mutation(self, task_lower: str) -> bool:
        """Check if task is a self-mutation (updating own profile)."""
        return any(re.search(p, task_lower) for p in _SELF_MUTATION_PATTERNS)

    def _extract_keywords(self, task_text: str) -> Set[str]:
        """Extract meaningful keywords from task text."""
        words = set(re.findall(r'\w+', task_text.lower()))
        return words - _TASK_STOPWORDS

    def _find_matching_files(
        self,
        wiki_paths: Any,
        task_words: Set[str],
        skip_critical: bool
    ) -> List[Tuple[str, Set[str]]]:
        """
        Find wiki files whose names match task keywords.

        Returns:
            List of (path, matching_words) tuples, sorted by match count desc
        """
        matching_files = []

        for wiki_path in wiki_paths:
            # Skip critical docs if requested
            if skip_critical and wiki_path in _CRITICAL_PATHS:
                continue

            # Extract filename words
            filename = wiki_path.replace('.md', '').replace('_', ' ').replace('/', ' ').lower()
            filename_words = set(re.findall(r'\w+', filename))

            # Check overlap
            overlap = task_words & filename_words
            if overlap and len(overlap) >= 1:
                matching_files.append((wiki_path, overlap))

        # Sort by overlap count (descending)
        matching_files.sort(key=lambda x: len(x[1]), reverse=True)
        return matching_files[:3]  # Max 3 suggestions

    def _format_hint(
        self,
        matching_files: List[Tuple[str, Set[str]]],
        context: str
    ) -> str:
        """Format matching files into a hint string."""
        hint_lines = []
        for path, overlap in matching_files:
            hint_lines.append(f"  - `{path}` (matches: {', '.join(overlap)})")

        if context == "wiki_change":
            return (
                f"\n💡 TASK-SPECIFIC FILES: Beyond the critical docs above, these wiki files match your task keywords:\n"
                + "\n".join(hint_lines)
                + f"\n⚠️ You should `wiki_load(\"{matching_files[0][0]}\")` for topic-specific details NOT covered in summaries above."
            )
        else:  # wiki_list
            return (
                f"\n💡 TASK-RELEVANT FILES: These wiki files match your task keywords:\n"
                + "\n".join(hint_lines)
                + f"\nConsider loading them with `wiki_load(\"{matching_files[0][0]}\")` for relevant info."
            )
