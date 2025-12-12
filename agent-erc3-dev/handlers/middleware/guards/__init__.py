"""
Response Guards - middleware that intercepts Req_ProvideAgentResponse.

Guards are organized by domain:
- outcome_guards.py: Validates outcome type (denied, not_found, clarification)
- project_guards.py: Project-related validation (search, modifications)
- time_guards.py: Time logging validation
- security_guards.py: Public user and security-related checks
- response_guards.py: General response validation
"""

from .outcome_guards import (
    AmbiguityGuardMiddleware,
    OutcomeValidationMiddleware,
    SingleCandidateOkHint,
    SubjectiveQueryGuard,
)

from .project_guards import (
    ProjectSearchReminderMiddleware,
    ProjectModificationClarificationGuard,
)

from .time_guards import (
    TimeLoggingClarificationGuard,
)

from .security_guards import (
    BasicLookupDenialGuard,
    PublicUserSemanticGuard,
)

from .response_guards import (
    ResponseValidationMiddleware,
)

__all__ = [
    # Outcome Guards
    'AmbiguityGuardMiddleware',
    'OutcomeValidationMiddleware',
    'SingleCandidateOkHint',
    'SubjectiveQueryGuard',
    # Project Guards
    'ProjectSearchReminderMiddleware',
    'ProjectModificationClarificationGuard',
    # Time Guards
    'TimeLoggingClarificationGuard',
    # Security Guards
    'BasicLookupDenialGuard',
    'PublicUserSemanticGuard',
    # Response Guards
    'ResponseValidationMiddleware',
]
