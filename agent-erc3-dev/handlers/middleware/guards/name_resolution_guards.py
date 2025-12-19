"""
Name Resolution Guards - ensure human names are resolved to IDs before comparisons.

Guards:
- NameResolutionGuard: Detects unresolved human names in task when comparing team members.

AICODE-NOTE: Critical fix for t007, t008. Agent searches projects and compares team member IDs
(e.g., FphR_051) against human names (e.g., "Alfano Vittorio") without first resolving
the name to an ID via employees_search.
"""
import re
from typing import Optional, List, Tuple
from ..base import ResponseGuard, get_task_text
from ...base import ToolContext
from utils import CLI_GREEN, CLI_CLR


class NameResolutionGuard(ResponseGuard):
    """
    Detects when agent responds ok_not_found but never resolved human names to IDs.

    Problem pattern (t007, t008):
    1. Task: "In which of my projects is Alfano Vittorio involved"
    2. Agent searches projects, gets team with IDs: FphR_051, FphR_118
    3. Agent compares "Alfano Vittorio" against IDs - no match
    4. Agent responds ok_not_found - WRONG! Should have searched employees first

    Solution: If task contains human name AND agent never called employees_search
    with that name -> soft block and remind to resolve name first.
    """

    # AICODE-NOTE: Must check both ok_not_found AND ok_answer because agent might:
    # 1. Guess wrong ID and say ok_not_found (original problem)
    # 2. Guess wrong ID and say ok_answer with wrong project (t007 regression)
    target_outcomes = {"ok_not_found", "ok_answer"}
    require_public = False  # Only for authenticated users

    # Pattern: Two capitalized words (First Last) that are NOT system IDs
    # Excludes: proj_xxx, emp_xxx, cust_xxx, FphR_xxx, SrwB_xxx, etc.
    # AICODE-NOTE: Extended to support diacritics (é, ü, ć, ž, etc.) common in European names
    # Uses Unicode ranges for Latin Extended-A and Latin-1 Supplement to cover all EU languages
    HUMAN_NAME_PATTERN = re.compile(
        r'([A-Z\u00C0-\u017F][a-z\u00E0-\u017F]+)\s+([A-Z\u00C0-\u017F][a-z\u00E0-\u017F]+)'
    )

    # System ID patterns to exclude
    SYSTEM_ID_PATTERNS = [
        r'^[A-Z][a-z]{2,3}[A-Z]_\d+$',  # FphR_015, SrwB_100
        r'^proj_',
        r'^cust_',
        r'^emp_',
        r'^[a-z]+_[a-z]+$',  # snake_case IDs like jonas_weiss
    ]

    # Keywords indicating the task is about finding someone in projects/teams
    TEAM_SEARCH_KEYWORDS = [
        r'\bproject[s]?\b.*\b(?:involved|member|team|work)',
        r'\binvolved\b.*\bproject',
        r'\bwhich\b.*\bproject',
        r'\bmy project[s]?\b',
        r'\bteam\b.*\b(?:member|include|has)',
    ]

    def __init__(self):
        self._system_id_re = [re.compile(p) for p in self.SYSTEM_ID_PATTERNS]
        self._team_search_re = re.compile('|'.join(self.TEAM_SEARCH_KEYWORDS), re.IGNORECASE)

    def _check(self, ctx: ToolContext, outcome: str) -> None:
        task_text = get_task_text(ctx)
        if not task_text:
            return

        # Only trigger for team/project member searches
        if not self._team_search_re.search(task_text):
            return

        # Extract human names from task
        human_names = self._extract_human_names(task_text)
        if not human_names:
            return

        # Check if employees_search was called with any of these names
        action_types_executed = ctx.shared.get('action_types_executed', set())
        employees_search_queries = ctx.shared.get('employees_search_queries', [])

        if 'employees_search' not in action_types_executed:
            # Never searched employees at all
            self._emit_hint(ctx, human_names, searched=False)
            return

        # Check if any of the human names were searched
        names_searched = self._check_names_searched(human_names, employees_search_queries)
        if names_searched:
            # Name was resolved - allow through
            print(f"  {CLI_GREEN}✓ NameResolutionGuard: Name resolved via employees_search{CLI_CLR}")
            return

        # Name not searched - but allow if already warned (soft block behavior)
        self._emit_hint(ctx, human_names, searched=True)

    def _extract_human_names(self, text: str) -> List[Tuple[str, str]]:
        """
        Extract human names (First Last) from text, excluding system IDs.

        Returns list of (first_name, last_name) tuples.
        """
        matches = self.HUMAN_NAME_PATTERN.findall(text)
        human_names = []

        for first, last in matches:
            full_name = f"{first} {last}"
            combined = f"{first}_{last}"

            # Skip if looks like a system ID
            is_system_id = any(p.match(full_name) or p.match(combined)
                               for p in self._system_id_re)
            if is_system_id:
                continue

            # Skip common non-name words
            skip_words = {'Human', 'Resources', 'Project', 'Customer', 'Account', 'Sales'}
            if first in skip_words or last in skip_words:
                continue

            human_names.append((first, last))

        return human_names

    def _check_names_searched(self, human_names: List[Tuple[str, str]],
                               search_queries: List[str]) -> bool:
        """Check if any human name was included in employees_search queries."""
        for first, last in human_names:
            name_variants = [
                f"{first} {last}",
                f"{last} {first}",
                first,
                last,
                f"{first.lower()}_{last.lower()}",
                f"{last.lower()}_{first.lower()}",
            ]

            for query in search_queries:
                query_lower = query.lower()
                for variant in name_variants:
                    if variant.lower() in query_lower:
                        return True

        return False

    def _emit_hint(self, ctx: ToolContext, human_names: List[Tuple[str, str]],
                   searched: bool) -> None:
        """Emit soft block hint to resolve names first."""
        names_str = ', '.join(f"{f} {l}" for f, l in human_names)

        if not searched:
            detail = "you **never called `employees_search`** to resolve the name to an ID"
        else:
            detail = "you called `employees_search` but **not with this person's name**"

        self._soft_block(
            ctx,
            warning_key='name_resolution_warned',
            log_msg=f"NameResolutionGuard: Human name '{names_str}' not resolved to ID",
            block_msg=(
                f"⚠️ **NAME RESOLUTION REQUIRED**: Task mentions '{names_str}' but {detail}!\n\n"
                f"**CRITICAL**: Employee IDs in this system are NOT `firstname_lastname` format!\n"
                f"They use codes like `FphR_015`, `SrwB_100`, etc.\n\n"
                f"**BEFORE comparing team members**, you MUST:\n"
                f"1. Call `employees_search(query=\"{human_names[0][0]} {human_names[0][1]}\")` to find the ID\n"
                f"2. Use the returned ID (e.g., `FphR_051`) to check project teams\n\n"
                f"**If you've already verified** the person doesn't exist, call respond again."
            )
        )
