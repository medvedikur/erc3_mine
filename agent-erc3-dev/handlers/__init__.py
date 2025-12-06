from .core import ActionExecutor
from .wiki import WikiManager, WikiMiddleware
from .security import SecurityManager, SecurityMiddleware
from .safety import (
    ProjectMembershipMiddleware,
    ResponseValidationMiddleware,
    AmbiguityGuardMiddleware,
    TimeLoggingClarificationGuard,
    OutcomeValidationMiddleware,
)

def get_executor(api, wiki_manager: WikiManager, security_manager: SecurityManager, task=None):
    middleware = [
        WikiMiddleware(wiki_manager),
        ProjectMembershipMiddleware(),
        AmbiguityGuardMiddleware(),           # Catches ambiguous queries with wrong outcome
        TimeLoggingClarificationGuard(),      # Ensures time log clarifications include project
        OutcomeValidationMiddleware(),        # Validates denied outcomes (unsupported vs security)
        ResponseValidationMiddleware(),       # Validates respond has proper message/links
    ]
    if security_manager:
        middleware.append(SecurityMiddleware(security_manager))

    return ActionExecutor(
        api=api,
        middleware=middleware,
        task=task
    )
