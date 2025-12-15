"""
Typed context for action handling.

Replaces Dict[str, Any] shared state with typed dataclasses
for better IDE support, type safety, and documentation.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, TYPE_CHECKING

if TYPE_CHECKING:
    from erc3 import TaskInfo
    from .security import SecurityManager
    from .wiki import WikiManager
    from stats import FailureLogger


@dataclass
class SharedState:
    """
    Typed shared state for tool context.

    Replaces the untyped Dict[str, Any] with explicit fields.
    This provides:
    - IDE autocomplete
    - Type checking
    - Documentation of available state
    - Default values

    AICODE-NOTE: This replaces ctx.shared Dict[str, Any] pattern.
    All fields that were previously accessed via ctx.shared['key']
    are now typed attributes.
    """

    # === Required Context ===
    security_manager: Optional['SecurityManager'] = None
    wiki_manager: Optional['WikiManager'] = None
    task: Optional['TaskInfo'] = None
    task_id: Optional[str] = None
    api: Optional[Any] = None

    # === Logging ===
    failure_logger: Optional['FailureLogger'] = None

    # === Mutation Tracking ===
    had_mutations: bool = False
    mutation_entities: List[Dict[str, Any]] = field(default_factory=list)

    # === Search Tracking (for auto-linking) ===
    search_entities: List[Dict[str, Any]] = field(default_factory=list)

    # === Validation State ===
    missing_tools: List[str] = field(default_factory=list)
    action_types_executed: Set[str] = field(default_factory=set)
    outcome_validation_warned: bool = False

    # === Pending Mutations ===
    pending_mutation_tools: Set[str] = field(default_factory=set)

    # === Enricher State ===
    # Definitive hints from overlap analyzer (persist across turns)
    overlap_definitive_hints: Dict[str, str] = field(default_factory=dict)

    # Query specificity from parser
    query_specificity: Optional[str] = None

    # === Specialized Handler Results ===
    # Pre-processed results from WikiSearchHandler, ProjectSearchHandler, etc.
    # These are consumed by PipelineExecutor
    _search_error: Optional[Exception] = None
    _project_search_result: Optional[Any] = None
    _employee_search_result: Optional[Any] = None

    # === Time Entry Update State ===
    time_update_entities: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert to dict for backward compatibility.

        Used when interfacing with code that expects ctx.shared dict.
        """
        return {
            'security_manager': self.security_manager,
            'wiki_manager': self.wiki_manager,
            'task': self.task,
            'task_id': self.task_id,
            'api': self.api,
            'failure_logger': self.failure_logger,
            'had_mutations': self.had_mutations,
            'mutation_entities': self.mutation_entities,
            'search_entities': self.search_entities,
            'missing_tools': self.missing_tools,
            'action_types_executed': self.action_types_executed,
            'outcome_validation_warned': self.outcome_validation_warned,
            'pending_mutation_tools': self.pending_mutation_tools,
            '_overlap_definitive_hints': self.overlap_definitive_hints,
            'query_specificity': self.query_specificity,
            '_search_error': self._search_error,
            '_project_search_result': self._project_search_result,
            '_employee_search_result': self._employee_search_result,
            'time_update_entities': self.time_update_entities,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SharedState':
        """
        Create from dict for backward compatibility.

        Used when receiving initial shared state as dict.
        """
        state = cls()

        # Map dict keys to attributes
        if 'security_manager' in data:
            state.security_manager = data['security_manager']
        if 'wiki_manager' in data:
            state.wiki_manager = data['wiki_manager']
        if 'task' in data:
            state.task = data['task']
        if 'task_id' in data:
            state.task_id = data['task_id']
        if 'api' in data:
            state.api = data['api']
        if 'failure_logger' in data:
            state.failure_logger = data['failure_logger']
        if 'had_mutations' in data:
            state.had_mutations = data['had_mutations']
        if 'mutation_entities' in data:
            state.mutation_entities = data['mutation_entities']
        if 'search_entities' in data:
            state.search_entities = data['search_entities']
        if 'missing_tools' in data:
            state.missing_tools = data['missing_tools']
        if 'action_types_executed' in data:
            state.action_types_executed = data['action_types_executed']
        if 'outcome_validation_warned' in data:
            state.outcome_validation_warned = data['outcome_validation_warned']
        if 'pending_mutation_tools' in data:
            state.pending_mutation_tools = data['pending_mutation_tools']
        if '_overlap_definitive_hints' in data:
            state.overlap_definitive_hints = data['_overlap_definitive_hints']
        if 'query_specificity' in data:
            state.query_specificity = data['query_specificity']
        if '_search_error' in data:
            state._search_error = data['_search_error']
        if '_project_search_result' in data:
            state._project_search_result = data['_project_search_result']
        if '_employee_search_result' in data:
            state._employee_search_result = data['_employee_search_result']
        if 'time_update_entities' in data:
            state.time_update_entities = data['time_update_entities']

        return state


class SharedStateProxy(dict):
    """
    Dict-like proxy for SharedState.

    Provides backward compatibility with code that expects ctx.shared
    to be a dict while actually using typed SharedState underneath.

    Usage:
        ctx.shared['security_manager']  # reads from state.security_manager
        ctx.shared['custom_key'] = val  # stores in overflow dict
    """

    # Known keys that map to SharedState attributes
    _KNOWN_KEYS = {
        'security_manager', 'wiki_manager', 'task', 'task_id', 'api',
        'failure_logger', 'had_mutations', 'mutation_entities',
        'search_entities', 'missing_tools', 'action_types_executed',
        'outcome_validation_warned', 'pending_mutation_tools',
        '_overlap_definitive_hints', 'query_specificity',
        '_search_error', '_project_search_result', '_employee_search_result',
        'time_update_entities',
    }

    # Map dict keys to attribute names
    _KEY_TO_ATTR = {
        '_overlap_definitive_hints': 'overlap_definitive_hints',
    }

    def __init__(self, state: SharedState):
        super().__init__()
        self._state = state

    def _get_attr_name(self, key: str) -> str:
        """Get attribute name for a key."""
        return self._KEY_TO_ATTR.get(key, key)

    def __getitem__(self, key: str) -> Any:
        if key in self._KNOWN_KEYS:
            attr = self._get_attr_name(key)
            return getattr(self._state, attr)
        return super().__getitem__(key)

    def __setitem__(self, key: str, value: Any) -> None:
        if key in self._KNOWN_KEYS:
            attr = self._get_attr_name(key)
            setattr(self._state, attr, value)
        else:
            super().__setitem__(key, value)

    # Keys that should be considered "not present" when empty/False
    # This allows merge to overwrite them with initial_shared values
    _MERGEABLE_WHEN_EMPTY = {
        'action_types_executed',  # Set - needs to merge from AgentTurnState
        'outcome_validation_warned',  # Bool - needs to merge True values
        'time_log_auth_warned',  # Bool - needs to merge True values
        'time_log_clarification_warned',  # Bool - needs to merge True values
        'single_candidate_warned',  # Bool - needs to merge True values
        'subjective_query_warned',  # Bool - needs to merge True values
        'ambiguity_employee_warned',  # Bool - needs to merge True values
    }

    def __contains__(self, key: object) -> bool:
        if key in self._KNOWN_KEYS:
            attr = self._get_attr_name(str(key))
            value = getattr(self._state, attr)
            # None always means not set
            if value is None:
                return False
            # For specific keys that need merge behavior, empty/False = not set
            if key in self._MERGEABLE_WHEN_EMPTY:
                if isinstance(value, (set, list, dict)) and not value:
                    return False
                if isinstance(value, bool) and not value:
                    return False
            return True
        return super().__contains__(key)

    def get(self, key: str, default: Any = None) -> Any:
        if key in self._KNOWN_KEYS:
            attr = self._get_attr_name(key)
            value = getattr(self._state, attr)
            return value if value is not None else default
        return super().get(key, default)

    def pop(self, key: str, *args) -> Any:
        """Pop a value, resetting to default for known keys."""
        if key in self._KNOWN_KEYS:
            attr = self._get_attr_name(key)
            value = getattr(self._state, attr)
            # Reset to default
            if attr in ('_search_error', '_project_search_result', '_employee_search_result'):
                setattr(self._state, attr, None)
            return value
        return super().pop(key, *args)

    def keys(self):
        """Return all keys including state attributes."""
        all_keys = set(self._KNOWN_KEYS)
        all_keys.update(super().keys())
        return all_keys

    def items(self):
        """Return all items including state attributes."""
        result = []
        for key in self._KNOWN_KEYS:
            attr = self._get_attr_name(key)
            result.append((key, getattr(self._state, attr)))
        result.extend(super().items())
        return result

    def update(self, other: Dict[str, Any] = None, **kwargs) -> None:
        """Update from dict."""
        if other:
            for key, value in other.items():
                self[key] = value
        for key, value in kwargs.items():
            self[key] = value

    def setdefault(self, key: str, default: Any = None) -> Any:
        """Set default value if key not present, return current value."""
        if key in self._KNOWN_KEYS:
            attr = self._get_attr_name(key)
            value = getattr(self._state, attr)
            # For mutable defaults (dict, list, set), return the actual object from state
            # so mutations are reflected
            if value is None or (isinstance(value, (dict, list, set)) and not value):
                setattr(self._state, attr, default)
                return default
            return value
        # For unknown keys, use standard dict behavior
        if key not in self:
            self[key] = default
        return self[key]

    @property
    def state(self) -> SharedState:
        """Access underlying typed state."""
        return self._state
