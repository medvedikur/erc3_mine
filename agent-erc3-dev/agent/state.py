"""
Agent turn state management.

Provides the AgentTurnState dataclass that tracks mutable state across
agent turns and actions within a task execution.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Any, Set, Dict


@dataclass
class AgentTurnState:
    """
    Mutable state shared across actions within a task execution.

    This replaces the ad-hoc DummyContext class and provides typed access
    to all state that persists across turns and actions.
    """
    # Required - set at initialization
    security_manager: Any  # SecurityManager instance

    # Turn tracking for budget awareness
    current_turn: int = 0  # 0-indexed turn number
    max_turns: int = 20  # Maximum turns allowed

    # Mutation tracking (persists across turns)
    had_mutations: bool = False
    mutation_entities: List[Dict] = field(default_factory=list)

    # Search tracking (for auto-linking in read-only operations)
    search_entities: List[Dict] = field(default_factory=list)

    # AICODE-NOTE: t003 FIX - Track explicitly fetched entities via GET calls.
    # These entities should be auto-linked for ok_answer even if not mentioned in message.
    # Use case: Agent answers "from Sales dept" but doesn't mention employee ID in text.
    fetched_entities: List[Dict] = field(default_factory=list)

    # AICODE-NOTE: t069 FIX - Track project leads found via projects_get.
    # When task asks to create wiki pages for leads, we validate all were created.
    found_project_leads: Set[str] = field(default_factory=set)

    # AICODE-NOTE: t016 FIX - Track leads of ACTIVE projects only.
    # For salary comparison queries ("leads with salary > X"), only active project leads count.
    # Format: Set of employee IDs who are leads of active projects
    active_project_leads: Set[str] = field(default_factory=set)

    # AICODE-NOTE: t016 FIX - Track salary of employees fetched via employees_get.
    # For "project leads with salary > X" queries, we validate all matching leads are in links.
    # Format: {employee_id: salary}
    fetched_employee_salaries: Dict[str, int] = field(default_factory=dict)

    # AICODE-NOTE: t016 FIX - Track projects found via projects_search but not yet processed.
    # For comprehensive lead queries, agent must call projects_get for ALL found projects.
    # Set of project IDs found through projects_search
    found_projects_search: Set[str] = field(default_factory=set)
    # Set of project IDs actually processed via projects_get
    processed_projects_get: Set[str] = field(default_factory=set)

    # AICODE-NOTE: t016 FIX - Track baseline employee for salary comparison queries.
    # Extracted from task text: "salary higher than [Name]"
    # This prevents agent from confusing similar names (e.g., Alessia vs Alessandro)
    salary_comparison_baseline_name: Optional[str] = field(default=None)
    salary_comparison_baseline_id: Optional[str] = field(default=None)
    salary_comparison_baseline_salary: Optional[int] = field(default=None)

    # AICODE-NOTE: t013 FIX - Track entity locations for 'send to' exclusion logic
    # Format: {entity_id: location_string}
    entity_locations: Dict[str, str] = field(default_factory=dict)

    # Validation tracking
    missing_tools: List[str] = field(default_factory=list)
    action_types_executed: Set[str] = field(default_factory=set)
    action_counts: Dict[str, int] = field(default_factory=dict)  # Track call counts per action type
    outcome_validation_warned: bool = False

    # AICODE-NOTE: Track employees_search queries for name resolution guard (t007, t008)
    # Allows NameResolutionGuard to verify if agent searched for person's name
    employees_search_queries: List[str] = field(default_factory=list)

    # Pending mutations (mutation tools planned but not yet executed)
    pending_mutation_tools: Set[str] = field(default_factory=set)

    # AICODE-NOTE: t077 FIX - Track query subjects (coachees/mentees) to filter from links
    query_subject_ids: Set[str] = field(default_factory=set)

    # AICODE-NOTE: t077 FIX - Track if coaching skill search was performed
    # For coaching queries, agent MUST search for employees with high skill levels
    # This flag is set when employees_search with skills filter returns results
    coaching_skill_search_done: bool = False
    coaching_skill_search_results: int = 0  # Count of potential coaches found

    # AICODE-NOTE: t067 FIX - Track wiki files "deleted" (content set to empty)
    # These should be filtered from links in rename operations
    deleted_wiki_files: Set[str] = field(default_factory=set)

    # AICODE-NOTE: t067 FIX - Store loaded wiki content for rename operations
    # LLM may corrupt Unicode when copying content; we use original instead
    loaded_wiki_content: Dict[str, str] = field(default_factory=dict)
    # AICODE-NOTE: t067 FIX - Store wiki content from API (preferred for rename consistency)
    loaded_wiki_content_api: Dict[str, str] = field(default_factory=dict)

    # AICODE-NOTE: t087 FIX - Track customer contact info for link extraction
    # When customers_get returns contact info, store it so response parser
    # can link customer when contact email/name is mentioned in response
    customer_contacts: Dict[str, Dict[str, str]] = field(default_factory=dict)

    # AICODE-NOTE: t037 FIX - Track employee notes updated via employees_update
    # For security guard to detect salary-related notes injection attacks
    # Format: {employee_id: note_text}
    employee_notes_updated: Dict[str, str] = field(default_factory=dict)

    # AICODE-NOTE: t029 FIX - Track projects where current user is Lead
    # "My projects" means projects where user is Lead, not just member
    # Format: Set of project IDs where user has role='Lead'
    user_lead_projects: Set[str] = field(default_factory=set)

    # Loop detection
    action_history: List[Any] = field(default_factory=list)

    # Enricher hints (persist across turns)
    overlap_definitive_hints: Dict[str, str] = field(default_factory=dict)

    # AICODE-NOTE: t076 FIX - Global skill/will level tracker for pagination
    # Persists across batch actions within a turn to accumulate data from all pages
    global_skill_level_tracker: Dict[str, Dict] = field(default_factory=dict)

    # AICODE-NOTE: t076 FIX - Persist interest superlative winners for response guard
    interest_superlative_answer_ids: List[str] = field(default_factory=list)

    # AICODE-NOTE: t009 FIX - Global workload tracker for pagination
    global_workload_tracker: Dict[str, tuple] = field(default_factory=dict)

    # AICODE-NOTE: t012 FIX - Global time_slice tracker from projects_get
    # When agent fetches many projects to calculate "busiest" employee via time_slice,
    # we accumulate: {employee_id: total_time_slice}
    # and show summary hint when done
    projects_get_time_slice_tracker: Dict[str, float] = field(default_factory=dict)
    # AICODE-NOTE: t012 FIX - Track processed project IDs to avoid double-counting
    projects_get_processed_ids: Set[str] = field(default_factory=set)

    # AICODE-NOTE: t010 FIX - Global tracker for least busy employee projects (persists across turns)
    least_busy_employee_projects: Dict[str, Dict] = field(default_factory=dict)

    # AICODE-NOTE: t010 FIX - Persist global least/busiest IDs for response guards
    least_busy_employee_ids: List[str] = field(default_factory=list)
    busiest_employee_ids: List[str] = field(default_factory=list)

    # AICODE-NOTE: t076 FIX - Track pending pagination to block premature responses
    # Format: {action_name: {'next_offset': int, 'current_count': int}}
    pending_pagination: Dict[str, Dict] = field(default_factory=dict)

    # AICODE-NOTE: Aggregator for member-based searches within a turn.
    # When agent does multiple projects_search(member=X) in one batch,
    # we aggregate results to show a clear mapping at the end.
    # Format: {employee_id: [project_ids]}
    member_projects_batch: Dict[str, List[str]] = field(default_factory=dict)

    # AICODE-NOTE: t069 FIX - Accumulate ALL project IDs from projects_search
    # When doing exhaustive project queries, LLM may lose track of some projects.
    # We accumulate IDs and show summary when pagination completes.
    accumulated_project_ids: List[str] = field(default_factory=list)

    # AICODE-NOTE: t013 FIX - Track single-result-at-max-level skill searches
    # Format: (skill_name, max_level, employee_id) or None
    # When agent finds exactly 1 employee at min_level=10, we track it
    # and require verification before allowing ok_answer
    single_result_max_level_skill: Optional[tuple] = field(default=None)
    skill_level_verification_done: bool = False

    # LLM response tracking (for criteria guards)
    last_thoughts: str = ""

    # References (set per-action)
    task: Optional[Any] = None
    api: Optional[Any] = None

    def to_shared_dict(self) -> Dict[str, Any]:
        """
        Convert state to shared dict format expected by middleware/tools.
        Used when creating context for parse_action and executor.
        """
        # Get current_user from security_manager for guards
        current_user = None
        if self.security_manager:
            current_user = getattr(self.security_manager, 'current_user', None)

        return {
            'security_manager': self.security_manager,
            'current_user': current_user,  # AICODE-NOTE: t029 FIX - For "my projects" filtering
            'had_mutations': self.had_mutations,
            'mutation_entities': self.mutation_entities,
            'search_entities': self.search_entities,
            'fetched_entities': self.fetched_entities,  # AICODE-NOTE: t003 FIX
            'missing_tools': self.missing_tools,
            'action_types_executed': self.action_types_executed,
            'action_counts': self.action_counts,
            'outcome_validation_warned': self.outcome_validation_warned,
            'employees_search_queries': self.employees_search_queries,
            'task': self.task,
            # AICODE-NOTE: t073 FIX - Required for parsers. Handle .task and .task_text
            'task_text': (getattr(self.task, 'task', '') or getattr(self.task, 'task_text', '') or str(self.task)) if self.task else '',
            '_overlap_definitive_hints': self.overlap_definitive_hints,
            'current_turn': self.current_turn,
            'max_turns': self.max_turns,
            'last_thoughts': self.last_thoughts,
            'member_projects_batch': self.member_projects_batch,
            # AICODE-NOTE: t076 FIX - Pass global tracker for batch pagination
            '_global_skill_level_tracker': self.global_skill_level_tracker,
            # AICODE-NOTE: t076 FIX - Pass interest superlative winners for response guard
            '_interest_superlative_answer_ids': self.interest_superlative_answer_ids,
            # AICODE-NOTE: t009 FIX - Pass global workload tracker for batch pagination
            '_global_workload_tracker': self.global_workload_tracker,
            # AICODE-NOTE: t012 FIX - Pass time_slice tracker for busiest employee calculation
            '_projects_get_time_slice_tracker': self.projects_get_time_slice_tracker,
            '_projects_get_processed_ids': self.projects_get_processed_ids,
            # AICODE-NOTE: t010 FIX - Pass least busy tracker
            '_least_busy_employee_projects': self.least_busy_employee_projects,
            # AICODE-NOTE: t010 FIX - Pass least/busiest IDs for response guards
            '_least_busy_employee_ids': self.least_busy_employee_ids,
            '_busiest_employee_ids': self.busiest_employee_ids,
            # AICODE-NOTE: t076 FIX - Pass pending pagination for IncompletePaginationGuard
            'pending_pagination': self.pending_pagination,
            # AICODE-NOTE: t077 FIX - Pass query subject IDs for link filtering
            'query_subject_ids': self.query_subject_ids,
            # AICODE-NOTE: t067 FIX - Pass deleted wiki files for link filtering
            'deleted_wiki_files': self.deleted_wiki_files,
            # AICODE-NOTE: t067 FIX - Pass loaded wiki content for rename operations
            '_loaded_wiki_content': self.loaded_wiki_content,
            # AICODE-NOTE: t067 FIX - Pass wiki content from API (preferred for rename)
            '_loaded_wiki_content_api': self.loaded_wiki_content_api,
            # AICODE-NOTE: t069 FIX - Pass found project leads for wiki creation guard
            'found_project_leads': self.found_project_leads,
            # AICODE-NOTE: t016 FIX - Pass active project leads for salary comparison
            'active_project_leads': self.active_project_leads,
            # AICODE-NOTE: t016 FIX - Pass fetched employee salaries for lead salary guard
            'fetched_employee_salaries': self.fetched_employee_salaries,
            # AICODE-NOTE: t087 FIX - Pass customer contacts for link extraction
            'customer_contacts': self.customer_contacts,
            # AICODE-NOTE: t069 FIX - Pass accumulated project IDs for summary hint
            'accumulated_project_ids': self.accumulated_project_ids,
            # AICODE-NOTE: t016 FIX - Pass project tracking for comprehensive lead queries
            'found_projects_search': self.found_projects_search,
            'processed_projects_get': self.processed_projects_get,
            # AICODE-NOTE: t016 FIX - Pass baseline employee for salary comparison
            'salary_comparison_baseline_name': self.salary_comparison_baseline_name,
            'salary_comparison_baseline_id': self.salary_comparison_baseline_id,
            'salary_comparison_baseline_salary': self.salary_comparison_baseline_salary,
            # AICODE-NOTE: t037 FIX - Pass employee notes for salary injection guard
            'employee_notes_updated': self.employee_notes_updated,
            # AICODE-NOTE: t029 FIX - Pass user lead projects for "my projects" filtering
            'user_lead_projects': self.user_lead_projects,
            # AICODE-NOTE: t077 FIX - Pass coaching search tracking for CoachingSearchGuard
            'coaching_skill_search_done': self.coaching_skill_search_done,
            'coaching_skill_search_results': self.coaching_skill_search_results,
            # AICODE-NOTE: t013 FIX - Pass entity locations for LocationExclusionGuard
            'entity_locations': self.entity_locations,
        }

    def clear_turn_aggregators(self) -> None:
        """Clear per-turn aggregators at the start of each turn."""
        self.member_projects_batch.clear()

    def create_context(self):
        """
        Create a context object compatible with parse_action and executor.
        Returns an object with .shared dict and .api reference.
        """
        class Context:
            def __init__(ctx_self, shared: Dict, api: Any):
                ctx_self.shared = shared
                ctx_self.api = api

        return Context(self.to_shared_dict(), self.api)

    def sync_from_context(self, ctx) -> None:
        """
        Sync state back from context after middleware execution.
        Middleware may modify ctx.shared, so we need to pull changes back.
        """
        if ctx.shared.get('outcome_validation_warned'):
            self.outcome_validation_warned = True

        # Sync overlap definitive hints (persist across turns)
        hints = ctx.shared.get('_overlap_definitive_hints')
        if hints:
            self.overlap_definitive_hints.update(hints)

        # AICODE-NOTE: t076 FIX - Sync global skill/will tracker for batch pagination
        tracker = ctx.shared.get('_global_skill_level_tracker')
        if tracker:
            self.global_skill_level_tracker.update(tracker)

        # AICODE-NOTE: t076 FIX - Sync interest superlative winners for response guard
        interest_answer_ids = ctx.shared.get('_interest_superlative_answer_ids')
        if interest_answer_ids is not None:
            self.interest_superlative_answer_ids = list(interest_answer_ids)

        # AICODE-NOTE: t009 FIX - Sync global workload tracker for batch pagination
        workload_tracker = ctx.shared.get('_global_workload_tracker')
        if workload_tracker:
            self.global_workload_tracker.update(workload_tracker)

        # AICODE-NOTE: t012 FIX - Sync time_slice tracker for busiest employee
        # The tracker already accumulates in the enricher, just sync it back
        time_slice_tracker = ctx.shared.get('_projects_get_time_slice_tracker')
        if time_slice_tracker:
            self.projects_get_time_slice_tracker = time_slice_tracker

        # AICODE-NOTE: t012 FIX - Sync processed project IDs to avoid double-counting
        processed_ids = ctx.shared.get('_projects_get_processed_ids')
        if processed_ids:
            self.projects_get_processed_ids = processed_ids

        # AICODE-NOTE: t010 FIX - Sync least busy tracker
        least_busy_tracker = ctx.shared.get('_least_busy_employee_projects')
        if least_busy_tracker:
            self.least_busy_employee_projects.update(least_busy_tracker)

        # AICODE-NOTE: t010 FIX - Sync least/busiest IDs for response guards
        least_busy_ids = ctx.shared.get('_least_busy_employee_ids')
        if least_busy_ids is not None:
            self.least_busy_employee_ids = list(least_busy_ids)
        busiest_ids = ctx.shared.get('_busiest_employee_ids')
        if busiest_ids is not None:
            self.busiest_employee_ids = list(busiest_ids)

        # AICODE-NOTE: t076 FIX - Sync pending pagination state
        # This is critical for IncompletePaginationGuard to work across actions
        pending_pagination = ctx.shared.get('pending_pagination')
        if pending_pagination is not None:
            self.pending_pagination = pending_pagination

        # AICODE-NOTE: t077 FIX - Sync query subject IDs
        # Enricher adds IDs when detecting coachee/mentee searches
        query_subjects = ctx.shared.get('query_subject_ids')
        if query_subjects:
            self.query_subject_ids.update(query_subjects)

        # AICODE-NOTE: t067 FIX - Sync deleted wiki files
        # For wiki rename operations, track files that were "deleted"
        deleted_wiki = ctx.shared.get('deleted_wiki_files')
        if deleted_wiki:
            self.deleted_wiki_files.update(deleted_wiki)

        # AICODE-NOTE: t067 FIX - Sync loaded wiki content
        # Store content from wiki_load for use in wiki_update (preserves Unicode)
        loaded_content = ctx.shared.get('_loaded_wiki_content')
        if loaded_content:
            self.loaded_wiki_content.update(loaded_content)

        # AICODE-NOTE: t067 FIX - Sync wiki content from API (preferred for rename)
        loaded_content_api = ctx.shared.get('_loaded_wiki_content_api')
        if loaded_content_api:
            self.loaded_wiki_content_api.update(loaded_content_api)

        # AICODE-NOTE: t087 FIX - Sync customer contacts for link extraction
        # Pipeline stores contact info from customers_get; response parser needs it
        customer_contacts = ctx.shared.get('customer_contacts')
        if customer_contacts:
            self.customer_contacts.update(customer_contacts)

        # AICODE-NOTE: t069 FIX - Sync accumulated project IDs
        accumulated_project_ids = ctx.shared.get('accumulated_project_ids')
        if accumulated_project_ids:
            # Extend (not replace) since we accumulate across multiple searches
            for pid in accumulated_project_ids:
                if pid not in self.accumulated_project_ids:
                    self.accumulated_project_ids.append(pid)

        # AICODE-NOTE: t037 FIX - Sync employee notes for salary injection guard
        # Pipeline stores notes from employees_update; guard checks them
        employee_notes_updated = ctx.shared.get('employee_notes_updated')
        if employee_notes_updated:
            self.employee_notes_updated.update(employee_notes_updated)

        # AICODE-NOTE: t029 FIX - Sync user lead projects
        # Pipeline/enricher adds project IDs where user is Lead
        user_lead_projects = ctx.shared.get('user_lead_projects')
        if user_lead_projects:
            self.user_lead_projects.update(user_lead_projects)

        # AICODE-NOTE: t077 FIX - Sync coaching skill search tracking
        # Employee search handler sets this when skills filter returns results
        if ctx.shared.get('coaching_skill_search_done'):
            self.coaching_skill_search_done = True
        coaching_results = ctx.shared.get('coaching_skill_search_results', 0)
        if coaching_results:
            self.coaching_skill_search_results += coaching_results

        # Note: had_mutations, mutation_entities, search_entities are
        # updated directly in the main loop after successful actions
