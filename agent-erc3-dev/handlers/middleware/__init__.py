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
    YesNoGuard,
    # Project Guards
    ProjectSearchReminderMiddleware,
    ProjectModificationClarificationGuard,
    ProjectTeamModAuthorizationGuard,
    ProjectStatusChangeAuthGuard,
    # Time Guards
    TimeLoggingClarificationGuard,
    TimeLoggingAuthorizationGuard,
    # Security Guards
    BasicLookupDenialGuard,
    PublicUserSemanticGuard,
    # Response Guards
    ResponseValidationMiddleware,
    LeadWikiCreationGuard,
    WorkloadFormatGuard,
    ContactEmailResponseGuard,
    ProjectLeadsSalaryComparisonGuard,
    SkillIdResponseGuard,
    ExternalProjectStatusGuard,
    SalaryNoteInjectionGuard,
    InternalProjectContactGuard,
    RecommendationLinksGuard,
    ComparisonTieLinksGuard,
    TieBreakerWinnerGuard,
    WorkloadExtremaLinksGuard,
    SingularProjectQueryGuard,
    SkillsIDontHaveGuard,
    MostSkilledVerificationGuard,
    CoachingSearchGuard,
    LocationExclusionGuard,
    ProjectLeadLinkGuard,
    # M&A Compliance Guards
    CCCodeValidationGuard,
    JiraTicketRequirementGuard,
    # Criteria Guards
    AddedCriteriaGuard,
    # Name Resolution Guards
    NameResolutionGuard,
    MultipleMatchClarificationGuard,
    # Pagination Guards
    PaginationEnforcementMiddleware,
    CustomerContactPaginationMiddleware,
    ProjectSearchOffsetGuard,
    CoachingTimeoutGuard,
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
    'YesNoGuard',
    'PaginationEnforcementMiddleware',
    'CustomerContactPaginationMiddleware',
    'ProjectSearchOffsetGuard',
    'CoachingTimeoutGuard',
    # Project Guards
    'ProjectSearchReminderMiddleware',
    'ProjectModificationClarificationGuard',
    'ProjectTeamModAuthorizationGuard',
    'ProjectStatusChangeAuthGuard',
    # Time Guards
    'TimeLoggingClarificationGuard',
    'TimeLoggingAuthorizationGuard',
    # Security Guards
    'BasicLookupDenialGuard',
    'PublicUserSemanticGuard',
    # Response Guards
    'ResponseValidationMiddleware',
    'LeadWikiCreationGuard',
    'WorkloadFormatGuard',
    'ContactEmailResponseGuard',
    'ProjectLeadsSalaryComparisonGuard',
    'SkillIdResponseGuard',
    'ExternalProjectStatusGuard',
    'SalaryNoteInjectionGuard',
    'InternalProjectContactGuard',
    'RecommendationLinksGuard',
    'ComparisonTieLinksGuard',
    'TieBreakerWinnerGuard',
    'WorkloadExtremaLinksGuard',
    'SingularProjectQueryGuard',
    'SkillsIDontHaveGuard',
    'MostSkilledVerificationGuard',
    'CoachingSearchGuard',
    'LocationExclusionGuard',
    'ProjectLeadLinkGuard',
    # M&A Compliance Guards
    'CCCodeValidationGuard',
    'JiraTicketRequirementGuard',
    # Criteria Guards
    'AddedCriteriaGuard',
    # Name Resolution Guards
    'NameResolutionGuard',
    'MultipleMatchClarificationGuard',
    # Membership
    'ProjectMembershipMiddleware',
]
