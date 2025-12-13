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

    # Mutation tracking (persists across turns)
    had_mutations: bool = False
    mutation_entities: List[Dict] = field(default_factory=list)

    # Search tracking (for auto-linking in read-only operations)
    search_entities: List[Dict] = field(default_factory=list)

    # Validation tracking
    missing_tools: List[str] = field(default_factory=list)
    action_types_executed: Set[str] = field(default_factory=set)
    outcome_validation_warned: bool = False

    # Pending mutations (mutation tools planned but not yet executed)
    pending_mutation_tools: Set[str] = field(default_factory=set)

    # Loop detection
    action_history: List[Any] = field(default_factory=list)

    # Enricher hints (persist across turns)
    overlap_definitive_hints: Dict[str, str] = field(default_factory=dict)

    # References (set per-action)
    task: Optional[Any] = None
    api: Optional[Any] = None

    def to_shared_dict(self) -> Dict[str, Any]:
        """
        Convert state to shared dict format expected by middleware/tools.
        Used when creating context for parse_action and executor.
        """
        return {
            'security_manager': self.security_manager,
            'had_mutations': self.had_mutations,
            'mutation_entities': self.mutation_entities,
            'search_entities': self.search_entities,
            'missing_tools': self.missing_tools,
            'action_types_executed': self.action_types_executed,
            'outcome_validation_warned': self.outcome_validation_warned,
            'task': self.task,
            '_overlap_definitive_hints': self.overlap_definitive_hints,
        }

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

        # Note: had_mutations, mutation_entities, search_entities are
        # updated directly in the main loop after successful actions
