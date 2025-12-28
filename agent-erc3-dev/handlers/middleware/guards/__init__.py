"""
Response Guards - middleware that intercepts Req_ProvideAgentResponse.

Guards are organized by domain:
- outcome_guards.py: Validates outcome type (denied, not_found, clarification)
- project_guards.py: Project-related validation (search, modifications)
- time_guards.py: Time logging validation
- security_guards.py: Public user and security-related checks
- response_guards.py: General response validation
- ma_compliance_guards.py: M&A compliance (CC codes, JIRA tickets)
- criteria_guards.py: Detect when agent adds criteria not in task
- name_resolution_guards.py: Ensure human names are resolved to IDs
"""

from .outcome_guards import (
    AmbiguityGuardMiddleware,
    OutcomeValidationMiddleware,
    SingleCandidateOkHint,
    SubjectiveQueryGuard,
    IncompletePaginationGuard,
    VagueQueryNotFoundGuard,
    YesNoGuard,
)

from .project_guards import (
    ProjectSearchReminderMiddleware,
    ProjectModificationClarificationGuard,
    ProjectTeamModAuthorizationGuard,
    ProjectStatusChangeAuthGuard,
)

from .time_guards import (
    TimeLoggingClarificationGuard,
    TimeLoggingAuthorizationGuard,
)

from .security_guards import (
    BasicLookupDenialGuard,
    PublicUserSemanticGuard,
)

from .response_guards import (
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
)

from .ma_compliance_guards import (
    CCCodeValidationGuard,
    JiraTicketRequirementGuard,
)

from .criteria_guards import (
    AddedCriteriaGuard,
)

from .name_resolution_guards import (
    NameResolutionGuard,
    MultipleMatchClarificationGuard,
)

from .pagination_guards import (
    PaginationEnforcementMiddleware,
    CustomerContactPaginationMiddleware,
    ProjectSearchOffsetGuard,
    CoachingTimeoutGuard,
)

__all__ = [
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
]
