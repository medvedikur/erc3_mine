from typing import Any, List, Set, Tuple
import re
from erc3.erc3 import client
from .base import ToolContext, Middleware

CLI_YELLOW = "\x1B[33m"
CLI_RED = "\x1B[31m"
CLI_GREEN = "\x1B[32m"
CLI_BLUE = "\x1B[34m"
CLI_CLR = "\x1B[0m"


class AmbiguityGuardMiddleware(Middleware):
    """
    Middleware that detects potentially ambiguous queries and asks the agent
    to reconsider when it responds with 'ok_not_found'.

    The problem: When a user asks "What's that cool project?", and the agent
    finds no projects, it might incorrectly respond with 'ok_not_found'.
    But the query itself is ambiguous ("that", "cool" are context-dependent),
    so the correct response should be 'none_clarification_needed'.

    This middleware:
    1. Detects ambiguity signals in task_text (referential words, subjective terms)
    2. When agent responds with 'ok_not_found', injects a warning asking to reconsider
    3. If agent confirms (responds again with same outcome), allows it through
    """

    # Patterns that suggest the query references external context we don't have
    REFERENTIAL_PATTERNS = [
        r'\bthat\b',           # "that project", "that person"
        r'\bthis\b',           # "this thing"
        r'\bthe one\b',        # "the one we discussed"
        r'\bsame\b',           # "same project as before"
        r'\bother\b',          # "the other one"
        r'\bprevious\b',       # "previous project"
        r'\blast\b',           # "last time"
        r'\byou know\b',       # "you know which one"
        r'\bwe (discussed|talked|mentioned)\b',  # references to prior conversation
    ]

    # Subjective terms that can't be resolved without user definition
    SUBJECTIVE_PATTERNS = [
        r'\bcool\b',
        r'\bbest\b',
        r'\bnice\b',
        r'\bgood\b',
        r'\bgreat\b',
        r'\binteresting\b',
        r'\bimportant\b',
        r'\bfavorite\b',
        r'\bfavourite\b',
        r'\bawesome\b',
        r'\bamazing\b',
        r'\btop\b',            # "top project"
        r'\bmain\b',           # "main project" (could be subjective)
        r'\bbig\b',            # "big project"
    ]

    def __init__(self):
        # Compile patterns for efficiency
        self._referential_re = re.compile(
            '|'.join(self.REFERENTIAL_PATTERNS),
            re.IGNORECASE
        )
        self._subjective_re = re.compile(
            '|'.join(self.SUBJECTIVE_PATTERNS),
            re.IGNORECASE
        )

    def _detect_ambiguity(self, text: str) -> Tuple[bool, List[str]]:
        """
        Analyze text for ambiguity signals.
        Returns (is_ambiguous, list_of_detected_signals)
        """
        if not text:
            return False, []

        signals = []

        # Check for referential patterns
        ref_matches = self._referential_re.findall(text)
        if ref_matches:
            signals.extend([f"referential: '{m}'" for m in ref_matches[:3]])

        # Check for subjective patterns
        subj_matches = self._subjective_re.findall(text)
        if subj_matches:
            signals.extend([f"subjective: '{m}'" for m in subj_matches[:3]])

        # Question about unspecified entity
        # e.g., "What's the name of..." without specific identifier
        if re.search(r"what'?s\s+(the\s+)?(name|id)\s+of", text, re.IGNORECASE):
            if not re.search(r'proj_|emp_|cust_|\w+_\w+', text):  # no specific ID
                signals.append("question: asks for name/id without specific identifier")

        return len(signals) > 0, signals

    def process(self, ctx: ToolContext) -> None:
        # Only intercept respond calls with ok_not_found
        if not isinstance(ctx.model, client.Req_ProvideAgentResponse):
            return

        outcome = ctx.model.outcome or ""
        if outcome != "ok_not_found":
            return

        # Get task text
        task = ctx.shared.get('task')
        task_text = getattr(task, 'task_text', '') if task else ''

        if not task_text:
            return

        # Check if we already warned about this
        ambiguity_warning_key = 'ambiguity_guard_warned'
        already_warned = ctx.shared.get(ambiguity_warning_key, False)

        # Detect ambiguity
        is_ambiguous, signals = self._detect_ambiguity(task_text)

        if not is_ambiguous:
            return

        if already_warned:
            # Agent saw the warning and still chose ok_not_found - respect the decision
            print(f"  {CLI_GREEN}✓ Ambiguity Guard: Agent confirmed 'ok_not_found' after warning{CLI_CLR}")
            return

        # First time seeing this - inject warning and block
        print(f"  {CLI_YELLOW}🤔 Ambiguity Guard: Detected ambiguous query with 'ok_not_found' response{CLI_CLR}")
        print(f"     Signals: {', '.join(signals)}")

        # Mark that we warned (so next time we allow through)
        ctx.shared[ambiguity_warning_key] = True

        # Stop execution and ask agent to reconsider
        ctx.stop_execution = True
        ctx.results.append(
            f"🤔 AMBIGUITY CHECK: I detected potential ambiguity in the user's request:\n"
            f"   Task: \"{task_text}\"\n"
            f"   Signals: {', '.join(signals)}\n\n"
            f"You responded with `ok_not_found`, but this might be incorrect.\n\n"
            f"**Consider**: Is the user asking about something SPECIFIC that doesn't exist? "
            f"Or is the query itself UNCLEAR/AMBIGUOUS (needs clarification)?\n\n"
            f"- If the query uses words like 'that', 'cool', 'best' without context, "
            f"the user might be referring to something you don't have information about.\n"
            f"- `ok_not_found` = \"I searched for X and it doesn't exist\"\n"
            f"- `none_clarification_needed` = \"I don't understand what you're asking for\"\n\n"
            f"**If you're confident `ok_not_found` is correct**, call respond again with the same outcome.\n"
            f"**If the query is ambiguous**, use `none_clarification_needed` and ask what they mean."
        )


class TimeLoggingClarificationGuard(Middleware):
    """
    Middleware that ensures time logging clarification requests include project links.

    Problem: When asking for CC codes or other clarifications for time logging,
    the agent might respond before identifying the project. But the benchmark
    expects the project link even in clarification responses.

    Solution: If task mentions time logging and agent responds with clarification
    without a project link, block and ask agent to identify the project first.
    """

    # Patterns that indicate time logging intent
    TIME_LOG_PATTERNS = [
        r'\blog\s+\d+\s*hours?\b',       # "log 3 hours"
        r'\b\d+\s*hours?\s+of\b',         # "3 hours of"
        r'\bbillable\s+work\b',           # "billable work"
        r'\blog\s+time\b',                # "log time"
        r'\btime\s+entry\b',              # "time entry"
        r'\btrack\s+time\b',              # "track time"
    ]

    def __init__(self):
        self._time_log_re = re.compile(
            '|'.join(self.TIME_LOG_PATTERNS),
            re.IGNORECASE
        )

    def _has_project_reference(self, message: str, links: list) -> bool:
        """Check if response contains project reference."""
        # Check links
        if links:
            for link in links:
                if isinstance(link, dict):
                    link_id = link.get('id', '')
                    link_kind = link.get('kind', '')
                    if link_id.startswith('proj_') or link_kind == 'project':
                        return True
                elif isinstance(link, str) and link.startswith('proj_'):
                    return True

        # Check message text for project ID pattern
        if message and re.search(r'proj_[a-z0-9_]+', message, re.IGNORECASE):
            return True

        return False

    def process(self, ctx: ToolContext) -> None:
        # Only intercept respond calls with none_clarification_needed
        if not isinstance(ctx.model, client.Req_ProvideAgentResponse):
            return

        outcome = ctx.model.outcome or ""
        if outcome != "none_clarification_needed":
            return

        # Get task text
        task = ctx.shared.get('task')
        task_text = getattr(task, 'task_text', '') if task else ''

        if not task_text:
            return

        # Check if this is a time logging task
        if not self._time_log_re.search(task_text):
            return

        message = ctx.model.message or ""
        links = ctx.model.links or []

        # Check if project is referenced
        if self._has_project_reference(message, links):
            return  # All good

        # Check if already warned
        warning_key = 'time_log_project_guard_warned'
        if ctx.shared.get(warning_key):
            # Already warned, let it through but log
            print(f"  {CLI_YELLOW}⚠️ TimeLog Guard: Agent confirmed clarification without project link{CLI_CLR}")
            return

        # First time - block and ask to identify project first
        print(f"  {CLI_YELLOW}🔍 TimeLog Guard: Clarification for time logging without project identification{CLI_CLR}")

        ctx.shared[warning_key] = True
        ctx.stop_execution = True
        ctx.results.append(
            f"🔍 TIME LOGGING GUARD: You're asking for clarification on a time logging task, "
            f"but you haven't identified the project yet!\n\n"
            f"**The benchmark expects project links even in clarification responses.**\n\n"
            f"Before asking for CC codes or other clarifications, you MUST:\n"
            f"1. Search for the employee's projects: `projects_search(member='employee_id')`\n"
            f"2. Identify which project matches (look for keywords in ID and name)\n"
            f"3. Check your authorization (are you Lead/AM/Manager?)\n"
            f"4. THEN ask for clarification, including the project ID in your message and links\n\n"
            f"Example correct response: \"I can log time for Felix (felix_baum) on 'Line 3 PoC' "
            f"(proj_acme_line3_cv_poc), but I need the CC code. What is it?\"\n\n"
            f"**If you've already searched and genuinely cannot identify the project**, "
            f"respond again with your clarification."
        )


class ResponseValidationMiddleware(Middleware):
    """
    Middleware that validates respond calls have proper message and links.
    Prevents agents from submitting empty responses after mutations.
    """
    def process(self, ctx: ToolContext) -> None:
        if not isinstance(ctx.model, client.Req_ProvideAgentResponse):
            return

        message = ctx.model.message or ""
        links = ctx.model.links or []
        outcome = ctx.model.outcome or ""

        # Check if mutations were performed this session
        had_mutations = ctx.shared.get('had_mutations', False)
        mutation_entities = ctx.shared.get('mutation_entities', [])

        # Validate: If mutations happened and outcome is ok_answer, message should describe what was done
        if had_mutations and outcome == "ok_answer":
            if message in ["", "No message provided.", "No message provided"]:
                print(f"  {CLI_YELLOW}⚠️ Response Validation: Empty message after mutation. Injecting summary...{CLI_CLR}")
                # Auto-generate a minimal message from mutation_entities
                entity_descriptions = []
                for entity in mutation_entities:
                    kind = entity.get("kind", "entity")
                    eid = entity.get("id", "unknown")
                    entity_descriptions.append(f"{kind}: {eid}")
                if entity_descriptions:
                    ctx.model.message = f"Action completed. Affected entities: {', '.join(entity_descriptions)}"
                    print(f"  {CLI_GREEN}✓ Auto-generated message: {ctx.model.message[:100]}...{CLI_CLR}")


class ProjectMembershipMiddleware(Middleware):
    """
    Middleware that verifies if an employee is a member of the project
    before allowing a time log entry.
    This prevents the agent from logging time to the wrong project just because
    the name matched partially.

    Also checks for M&A policy compliance (CC codes) if merger.md exists.
    """
    def process(self, ctx: ToolContext) -> None:
        # Intercept Time Logging
        if isinstance(ctx.model, client.Req_LogTimeEntry):
            employee_id = ctx.model.employee
            project_id = ctx.model.project

            # Skip check if arguments are missing (validation will catch it later)
            if not employee_id or not project_id:
                return

            print(f"  {CLI_YELLOW}🛡️ Safety Check: Verifying project membership...{CLI_CLR}")

            # CHECK M&A POLICY: If merger.md exists and requires CC codes, inject a warning
            wiki_manager = ctx.shared.get('wiki_manager')
            if wiki_manager and wiki_manager.has_page("merger.md"):
                merger_content = wiki_manager.get_page("merger.md")
                if merger_content and "cost centre" in merger_content.lower():
                    # M&A policy requires CC codes for time entries
                    # Check if task text mentions CC code or the notes contain one
                    task = ctx.shared.get('task')
                    task_text = (getattr(task, 'task_text', '') or '').lower() if task else ''
                    notes = (ctx.model.notes or '').upper()

                    # CC code format: CC-<Region>-<Unit>-<ProjectCode> e.g. CC-EU-AI-042
                    # Be flexible with format - accept any CC-XXX-XX-XXX pattern (letters/numbers)
                    # User might provide CC-NORD-AI-12O (with letter O instead of 0)
                    cc_pattern = r'CC-[A-Z0-9]{2,4}-[A-Z0-9]{2,4}-[A-Z0-9]{2,4}'
                    has_cc_in_task = bool(re.search(cc_pattern, task_text.upper()))
                    has_cc_in_notes = bool(re.search(cc_pattern, notes))

                    if not has_cc_in_task and not has_cc_in_notes:
                        print(f"  {CLI_RED}⚠️ M&A Policy: CC code required but not provided!{CLI_CLR}")
                        ctx.stop_execution = True
                        ctx.results.append(
                            f"⚠️ M&A POLICY VIOLATION: Per merger.md, all time entries now require a Cost Centre (CC) code. "
                            f"Format: CC-<Region>-<Unit>-<ProjectCode> (e.g., CC-EU-AI-042). "
                            f"You MUST ask the user for the CC code before logging time. "
                            f"Use `none_clarification_needed` to request the CC code from the user."
                        )
                        return
            
            try:
                # Fetch Project Details
                # We use the API available in context
                # Try positional arg first, then project_id/id kwarg to handle API variations
                try:
                    resp_project = ctx.api.get_project(project_id)
                except TypeError:
                    try:
                        resp_project = ctx.api.get_project(project_id=project_id)
                    except TypeError:
                        resp_project = ctx.api.get_project(id=project_id)
                
                # Check Membership
                # Resp_GetProject contains 'project' field which has 'team'
                project = getattr(resp_project, 'project', None)
                if not project:
                     # Project not found?
                     print(f"  {CLI_YELLOW}⚠️ Safety Check: Project '{project_id}' not found via API.{CLI_CLR}")
                     # Let the action proceed to fail naturally or handle as error?
                     # If we can't find it, we can't check membership. 
                     # But time_log will likely fail if project invalid.
                     return

                # Assuming project.team is a list of employee IDs or objects with 'id'
                is_member = False
                if project.team:
                    # Handle both list of strings and list of objects
                    for member in project.team:
                        if isinstance(member, str):
                            if member == employee_id:
                                is_member = True
                                break
                        elif hasattr(member, 'id') and member.id == employee_id:
                            is_member = True
                            break
                        elif hasattr(member, 'employee_id') and member.employee_id == employee_id:
                            is_member = True
                            break
                        elif hasattr(member, 'employee') and member.employee == employee_id:
                            is_member = True
                            break
                
                if not is_member:
                    print(f"  {CLI_RED}⛔ Safety Violation: Employee not in project.{CLI_CLR}")
                    ctx.stop_execution = True
                    ctx.results.append(
                        f"SAFETY ERROR: Employee '{employee_id}' is NOT a member of project '{project.name}' ({project_id}). "
                        f"You cannot log time for an employee on a project they are not assigned to. "
                        f"Please verify the project ID. You may need to search for other projects or check project details. "
                        f"Tip: You can use 'time_search' for this employee to see which projects they have worked on recently."
                    )
            
            except Exception as e:
                # If we can't verify (e.g. project not found or API error), 
                # we should probably fail safe or warn. 
                # Let's fail safe and block, as logging to non-existent project is also bad.
                print(f"  {CLI_RED}⚠️ Safety Check Failed: {e}{CLI_CLR}")
                # We don't block execution on API error, just warn? 
                # Or block? If get_project fails, time_log will likely fail too.
                # Let's let the actual handler try and fail naturally if it's a network error.
                pass

