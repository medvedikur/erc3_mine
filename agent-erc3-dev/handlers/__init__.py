from .core import ActionExecutor
from .wiki import WikiManager, WikiMiddleware
from .security import SecurityManager, SecurityMiddleware
from .safety import (
    ProjectMembershipMiddleware,
    ProjectSearchReminderMiddleware,
    ResponseValidationMiddleware,
    AmbiguityGuardMiddleware,
    TimeLoggingClarificationGuard,
    SingleCandidateOkHint,
    OutcomeValidationMiddleware,
    PublicUserSemanticGuard,
    BasicLookupDenialGuard,
    ProjectModificationClarificationGuard,
    SubjectiveQueryGuard,
)

def get_executor(api, wiki_manager: WikiManager, security_manager: SecurityManager, task=None):
    middleware = [
        WikiMiddleware(wiki_manager),
        ProjectMembershipMiddleware(),
        ProjectSearchReminderMiddleware(),            # Reminds to use projects_search for project queries
        AmbiguityGuardMiddleware(),                   # Catches ambiguous queries with wrong outcome
        TimeLoggingClarificationGuard(),              # Ensures time log clarifications include project
        SingleCandidateOkHint(),                      # Nudges ok_answer when single candidate found
        ProjectModificationClarificationGuard(),      # Ensures project mod clarifications include project link
        BasicLookupDenialGuard(),                     # Catches denied_security for basic org-chart lookups
        SubjectiveQueryGuard(),                       # Blocks ok_answer on subjective queries (cool, best, that)
        OutcomeValidationMiddleware(),                # Validates denied outcomes (unsupported vs security)
        PublicUserSemanticGuard(),                    # Ensures guests use denied_security for internal data
        ResponseValidationMiddleware(),               # Validates respond has proper message/links
    ]
    if security_manager:
        middleware.append(SecurityMiddleware(security_manager))

    return ActionExecutor(
        api=api,
        middleware=middleware,
        task=task
    )
