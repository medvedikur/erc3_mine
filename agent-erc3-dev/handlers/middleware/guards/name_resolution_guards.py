"""
Name Resolution Guards - ensure human names are resolved to IDs before comparisons.

Guards:
- NameResolutionGuard: Detects unresolved human names in task when comparing team members.
- MultipleMatchClarificationGuard: Detects when agent picks one from multiple name matches (t080).

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

        # AICODE-NOTE: Fix for t070, t071. Skip for CUSTOMER queries.
        # "Machina Press", "Carpathia Metalworkers" look like human names but are company names.
        # If task is about customers OR agent used customers_search, this guard doesn't apply.
        action_types_executed = ctx.shared.get('action_types_executed', set())
        if re.search(r'\bcustomer[s]?\b', task_text, re.IGNORECASE):
            print(f"  {CLI_GREEN}✓ NameResolutionGuard: Skipped - customer query{CLI_CLR}")
            return
        if 'customers_search' in action_types_executed:
            print(f"  {CLI_GREEN}✓ NameResolutionGuard: Skipped - customers_search was used{CLI_CLR}")
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


class MultipleMatchClarificationGuard(ResponseGuard):
    """
    AICODE-NOTE: t080 FIX - Detects when agent picks one employee from multiple name matches.

    Problem: Task asks about "Iva", employees_search returns 4 people named Iva,
    but agent picks one and responds ok_answer instead of asking for clarification.

    Trigger conditions:
    1. Task contains first name only (no last name, no ID)
    2. employees_search returned >1 result
    3. Agent responds with ok_answer mentioning only 1 of those employees
    4. Task is asking about a SPECIFIC person (not a list query)

    Solution: Soft block and require none_clarification_needed
    """

    target_outcomes = {"ok_answer"}
    require_public = None  # Both public and internal

    # Keywords indicating task asks about a SINGLE person (not listing)
    SINGLE_PERSON_KEYWORDS = [
        r'\bwhat is (?:the )?(?:department|salary|email|location|manager) of\b',
        r'\bwho is\b',
        r'\bfind\b.*\bemployee\b',
        r'\bwhich (?:department|team)\b',
        r'\bwhere (?:is|does)\b',
    ]

    # Keywords that suggest listing is OK (skip guard)
    LIST_QUERY_KEYWORDS = [
        r'\blist all\b',
        r'\bhow many\b',
        r'\bfind all\b',
        r'\beveryone\b',
        r'\ball employees?\b',
    ]

    def __init__(self):
        self._single_person_re = re.compile('|'.join(self.SINGLE_PERSON_KEYWORDS), re.IGNORECASE)
        self._list_query_re = re.compile('|'.join(self.LIST_QUERY_KEYWORDS), re.IGNORECASE)

    def _check(self, ctx: ToolContext, outcome: str) -> None:
        task_text = get_task_text(ctx)
        if not task_text:
            return

        # Skip list queries
        if self._list_query_re.search(task_text):
            return

        # Check if task is about a single person
        if not self._single_person_re.search(task_text):
            return

        # Check if task contains only first name (no last name)
        # Pattern: standalone word that could be a first name (capitalized, 2-15 chars)
        name_in_task = self._extract_standalone_name(task_text)
        if not name_in_task:
            return

        # Check if employees_search returned multiple results
        last_search_result = ctx.shared.get('_employee_search_result')
        if not last_search_result:
            return

        employees = getattr(last_search_result, 'employees', []) or []
        if len(employees) <= 1:
            # Only 1 or 0 employees found - no ambiguity
            return

        # Multiple employees found - check if agent only mentioned one in response
        message = ctx.model.message or ""
        employee_ids_in_response = self._extract_employee_ids(message)
        employee_names_in_response = self._extract_employee_names_from_message(message, employees)

        # Count how many of the found employees are mentioned in response
        mentioned_count = len(employee_ids_in_response) + len(employee_names_in_response)

        if mentioned_count <= 1 and len(employees) > 1:
            # Agent picked just one from multiple matches
            employee_list = []
            for emp in employees[:5]:  # Show max 5
                name = getattr(emp, 'name', 'Unknown')
                emp_id = getattr(emp, 'id', 'unknown')
                dept = getattr(emp, 'department', 'Unknown')
                employee_list.append(f"  • {name} ({emp_id}) - {dept}")

            if len(employees) > 5:
                employee_list.append(f"  ... and {len(employees) - 5} more")

            self._soft_block(
                ctx,
                warning_key='multiple_match_clarification_warned',
                log_msg=f"MultipleMatchClarificationGuard: {len(employees)} matches for '{name_in_task}', agent picked 1",
                block_msg=(
                    f"⚠️ **AMBIGUOUS NAME**: You found {len(employees)} employees matching '{name_in_task}', "
                    f"but responded with only ONE!\n\n"
                    f"**Found employees:**\n" + "\n".join(employee_list) + "\n\n"
                    f"**Since the user only provided a first name and multiple people match**, "
                    f"you MUST use `none_clarification_needed` and ask which person they mean.\n\n"
                    f"Example response:\n"
                    f'`"message": "I found {len(employees)} employees named {name_in_task}. Which one do you mean?", '
                    f'"outcome": "none_clarification_needed"`'
                )
            )

    def _extract_standalone_name(self, text: str) -> Optional[str]:
        """Extract a standalone first name from task text."""
        # Common patterns: "of Iva", "about Iva", "for Iva", "is Iva"
        patterns = [
            r'\bof\s+([A-Z][a-z]{1,15})\b(?!\s+[A-Z])',  # "of Iva" but not "of Iva Vidović"
            r'\babout\s+([A-Z][a-z]{1,15})\b(?!\s+[A-Z])',
            r'\bfor\s+([A-Z][a-z]{1,15})\b(?!\s+[A-Z])',
            r'\bis\s+([A-Z][a-z]{1,15})\b(?!\s+[A-Z])',
            r'\b([A-Z][a-z]{1,15})\s*$',  # Name at end of sentence
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                name = match.group(1)
                # Filter common non-names
                if name.lower() not in {'the', 'this', 'that', 'what', 'which', 'where', 'who', 'how'}:
                    return name
        return None

    def _extract_employee_ids(self, text: str) -> List[str]:
        """Extract employee IDs (like BwFV_100) from text."""
        pattern = r'\b([A-Z][a-z]{2,3}[A-Z]_\d+)\b'
        return re.findall(pattern, text)

    def _extract_employee_names_from_message(self, message: str, employees: list) -> List[str]:
        """Check which employee names from search results appear in message."""
        found = []
        message_lower = message.lower()
        for emp in employees:
            name = getattr(emp, 'name', '')
            if name and name.lower() in message_lower:
                found.append(name)
        return found
