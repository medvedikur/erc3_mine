from .core import ActionExecutor, DefaultActionHandler
from .wiki import WikiManager, WikiMiddleware, get_embedding_model
from .security import SecurityManager, SecurityMiddleware
from .context import SharedState, SharedStateProxy
from .pipeline import ActionPipeline
from .middleware import (
    ProjectMembershipMiddleware,
    ProjectSearchReminderMiddleware,
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
    AmbiguityGuardMiddleware,
    TimeLoggingClarificationGuard,
    TimeLoggingAuthorizationGuard,
    SingleCandidateOkHint,
    OutcomeValidationMiddleware,
    PublicUserSemanticGuard,
    BasicLookupDenialGuard,
    ProjectModificationClarificationGuard,
    ProjectTeamModAuthorizationGuard,
    ProjectStatusChangeAuthGuard,
    SubjectiveQueryGuard,
    IncompletePaginationGuard,
    VagueQueryNotFoundGuard,
    YesNoGuard,
    # Pagination Guards
    PaginationEnforcementMiddleware,
    CustomerContactPaginationMiddleware,
    ProjectSearchOffsetGuard,
    CoachingTimeoutGuard,
    # M&A Compliance
    CCCodeValidationGuard,
    JiraTicketRequirementGuard,
    # Criteria Guards
    AddedCriteriaGuard,
    # Name Resolution Guards
    NameResolutionGuard,
    MultipleMatchClarificationGuard,
    LocationExclusionGuard,
    ProjectLeadLinkGuard,
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
        YesNoGuard(),                                 # t022: Enforce English Yes/No
        IncompletePaginationGuard(),                  # Blocks ok_answer when LIST query has unfetched pages
        PaginationEnforcementMiddleware(),            # Blocks analysis tools when pagination is incomplete
        CustomerContactPaginationMiddleware(),        # t087: Blocks customers_get when customers_list incomplete
        ProjectSearchOffsetGuard(),                   # t069: Validates sequential offsets for projects_search
        CoachingTimeoutGuard(),                       # t077: Force respond on last turns for coaching queries
        NameResolutionGuard(),                        # Ensures human names resolved to IDs (t007, t008)
        MultipleMatchClarificationGuard(),            # t080: Requires clarification when multiple name matches
        OutcomeValidationMiddleware(),                # Validates denied outcomes (unsupported vs security)
        PublicUserSemanticGuard(),                    # Ensures guests use denied_security for internal data
        # M&A Compliance Guards
        CCCodeValidationGuard(),                      # Validates CC code format after M&A
        JiraTicketRequirementGuard(),                 # Requires JIRA ticket for project changes after M&A
        # Criteria Guards
        AddedCriteriaGuard(),                         # Warns when agent adds criteria not in task
        ResponseValidationMiddleware(),               # Validates respond has proper message/links
        LeadWikiCreationGuard(),                      # t069: Validates all leads have wiki pages
        WorkloadFormatGuard(),                        # t078: Auto-fix workload "0" -> "0.0"
        ContactEmailResponseGuard(),                  # t087: Block internal email for contact email queries
        ProjectLeadsSalaryComparisonGuard(),          # t016: Auto-add missing leads in salary comparison
        SkillIdResponseGuard(),                       # t094: Block raw skill IDs in response
        SkillsIDontHaveGuard(),                       # t094: Force ok_answer for 'skills I don't have' queries
        MostSkilledVerificationGuard(),               # t013: Force verification when single result at max level
        CoachingSearchGuard(),                        # t077: Require skill search before coaching response
        ExternalProjectStatusGuard(),                 # t053: Block project status change from External dept
        SalaryNoteInjectionGuard(),                   # t037: Block salary-related notes from non-executives
        InternalProjectContactGuard(),                # t026: Block ok_answer for internal project contact queries
        RecommendationLinksGuard(),                   # t056: Auto-add missing employee links in list queries
        ComparisonTieLinksGuard(),                    # t073: Ensure links on tie when task says "both if tied"
        TieBreakerWinnerGuard(),                      # t075: Auto-correct employee link to calculated winner
        WorkloadExtremaLinksGuard(),                  # t012: Enforce busiest/least-busy employee links from workload enrichment
        ProjectStatusChangeAuthGuard(),               # t054: Block ok_answer for status change without auth
        SingularProjectQueryGuard(),                  # t029: Force single project for singular queries
        LocationExclusionGuard(),                     # t013: Hint to exclude employees in target location for 'send to' tasks
        ProjectLeadLinkGuard(),                       # t000: Auto-add employee link for "who is lead" queries
    ]
    if security_manager:
        middleware.append(SecurityMiddleware(security_manager))

    return ActionExecutor(
        api=api,
        middleware=middleware,
        task=task
    )
