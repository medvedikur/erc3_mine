from .core import ActionExecutor, DefaultActionHandler
from .wiki import WikiManager, WikiMiddleware, get_embedding_model
from .security import SecurityManager, SecurityMiddleware
from .context import SharedState, SharedStateProxy
from .pipeline import ActionPipeline
from .middleware import (
    ProjectMembershipMiddleware,
    ProjectSearchReminderMiddleware,
    ResponseValidationMiddleware,
    AmbiguityGuardMiddleware,
    TimeLoggingClarificationGuard,
    TimeLoggingAuthorizationGuard,
    SingleCandidateOkHint,
    OutcomeValidationMiddleware,
    PublicUserSemanticGuard,
    BasicLookupDenialGuard,
    ProjectModificationClarificationGuard,
    ProjectTeamModAuthorizationGuard,
    SubjectiveQueryGuard,
    IncompletePaginationGuard,
    VagueQueryNotFoundGuard,
    # Pagination Guards
    PaginationEnforcementMiddleware,
    CustomerContactPaginationMiddleware,
    # M&A Compliance
    CCCodeValidationGuard,
    JiraTicketRequirementGuard,
    # Criteria Guards
    AddedCriteriaGuard,
    # Name Resolution Guards
    NameResolutionGuard,
)

def get_executor(api, wiki_manager: WikiManager, security_manager: SecurityManager, task=None):
    middleware = [
        WikiMiddleware(wiki_manager),
        ProjectMembershipMiddleware(),
        ProjectSearchReminderMiddleware(),            # Reminds to use projects_search for project queries
        ProjectTeamModAuthorizationGuard(),           # Requires projects_get before team modification response
        AmbiguityGuardMiddleware(),                   # Catches ambiguous queries with wrong outcome
        TimeLoggingClarificationGuard(),              # Ensures time log clarifications include project
        TimeLoggingAuthorizationGuard(),              # Requires projects_get before denying time log
        SingleCandidateOkHint(),                      # Nudges ok_answer when single candidate found
        ProjectModificationClarificationGuard(),      # Ensures project mod clarifications include project link
        BasicLookupDenialGuard(),                     # Catches denied_security for basic org-chart lookups
        SubjectiveQueryGuard(),                       # Blocks ok_answer on subjective queries (cool, best, that)
        VagueQueryNotFoundGuard(),                    # Blocks ok_not_found on vague queries (t005 fix)
        IncompletePaginationGuard(),                  # Blocks ok_answer when LIST query has unfetched pages
        PaginationEnforcementMiddleware(),            # Blocks analysis tools when pagination is incomplete
        CustomerContactPaginationMiddleware(),        # t087: Blocks customers_get when customers_list incomplete
        NameResolutionGuard(),                        # Ensures human names resolved to IDs (t007, t008)
        OutcomeValidationMiddleware(),                # Validates denied outcomes (unsupported vs security)
        PublicUserSemanticGuard(),                    # Ensures guests use denied_security for internal data
        # M&A Compliance Guards
        CCCodeValidationGuard(),                      # Validates CC code format after M&A
        JiraTicketRequirementGuard(),                 # Requires JIRA ticket for project changes after M&A
        # Criteria Guards
        AddedCriteriaGuard(),                         # Warns when agent adds criteria not in task
        ResponseValidationMiddleware(),               # Validates respond has proper message/links
    ]
    if security_manager:
        middleware.append(SecurityMiddleware(security_manager))

    return ActionExecutor(
        api=api,
        middleware=middleware,
        task=task
    )
