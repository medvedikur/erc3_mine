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
    IncompletePaginationGuard,
    VagueQueryNotFoundGuard,
    # Project Guards
    ProjectSearchReminderMiddleware,
    ProjectModificationClarificationGuard,
    ProjectTeamModAuthorizationGuard,
    # Time Guards
    TimeLoggingClarificationGuard,
    TimeLoggingAuthorizationGuard,
    # Security Guards
    BasicLookupDenialGuard,
    PublicUserSemanticGuard,
    # Response Guards
    ResponseValidationMiddleware,
    # M&A Compliance Guards
    CCCodeValidationGuard,
    JiraTicketRequirementGuard,
    # Criteria Guards
    AddedCriteriaGuard,
    # Name Resolution Guards
    NameResolutionGuard,
    # Pagination Guards
    PaginationEnforcementMiddleware,
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
    'IncompletePaginationGuard',
    'VagueQueryNotFoundGuard',
    'PaginationEnforcementMiddleware',
    # Project Guards
    'ProjectSearchReminderMiddleware',
    'ProjectModificationClarificationGuard',
    'ProjectTeamModAuthorizationGuard',
    # Time Guards
    'TimeLoggingClarificationGuard',
    'TimeLoggingAuthorizationGuard',
    # Security Guards
    'BasicLookupDenialGuard',
    'PublicUserSemanticGuard',
    # Response Guards
    'ResponseValidationMiddleware',
    # M&A Compliance Guards
    'CCCodeValidationGuard',
    'JiraTicketRequirementGuard',
    # Criteria Guards
    'AddedCriteriaGuard',
    # Name Resolution Guards
    'NameResolutionGuard',
    # Membership
    'ProjectMembershipMiddleware',
]
