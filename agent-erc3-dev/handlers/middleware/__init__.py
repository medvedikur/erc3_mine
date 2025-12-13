# Middleware module
# Re-exports for backwards compatibility

from .base import (
    ResponseGuard,
    get_task_text,
    is_public_user,
    has_project_reference,
)

# Import from guards submodule (refactored from response_guards.py)
from .guards import (
    # Outcome Guards
    AmbiguityGuardMiddleware,
    OutcomeValidationMiddleware,
    SingleCandidateOkHint,
    SubjectiveQueryGuard,
    # Project Guards
    ProjectSearchReminderMiddleware,
    ProjectModificationClarificationGuard,
    ProjectTeamModAuthorizationGuard,
    # Time Guards
    TimeLoggingClarificationGuard,
    # Security Guards
    BasicLookupDenialGuard,
    PublicUserSemanticGuard,
    # Response Guards
    ResponseValidationMiddleware,
    # M&A Compliance Guards
    CCCodeValidationGuard,
    JiraTicketRequirementGuard,
)

from .membership import ProjectMembershipMiddleware

__all__ = [
    # Base
    'ResponseGuard',
    'get_task_text',
    'is_public_user',
    'has_project_reference',
    # Outcome Guards
    'AmbiguityGuardMiddleware',
    'OutcomeValidationMiddleware',
    'SingleCandidateOkHint',
    'SubjectiveQueryGuard',
    # Project Guards
    'ProjectSearchReminderMiddleware',
    'ProjectModificationClarificationGuard',
    'ProjectTeamModAuthorizationGuard',
    # Time Guards
    'TimeLoggingClarificationGuard',
    # Security Guards
    'BasicLookupDenialGuard',
    'PublicUserSemanticGuard',
    # Response Guards
    'ResponseValidationMiddleware',
    # M&A Compliance Guards
    'CCCodeValidationGuard',
    'JiraTicketRequirementGuard',
    # Membership
    'ProjectMembershipMiddleware',
]
