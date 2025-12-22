"""
Enrichers module.

Provides classes that enrich API responses with helpful hints and analysis
to guide the agent's decision making.

Architecture:
- Enrichers are called after successful API responses
- They analyze results and return hint strings (or None)
- Composite enrichers (like ProjectSearchEnricher) combine multiple sub-enrichers
- Each enricher focuses on one aspect: ranking, authorization, search tips, etc.
"""
from .project_ranking import ProjectRankingEnricher
from .project_overlap import ProjectOverlapAnalyzer
from .project_search import ProjectSearchEnricher
from .wiki_hints import WikiHintEnricher
from .bonus_policy import BonusPolicyEnricher
from .efficiency_hints import EfficiencyHintEnricher
from .response_enrichers import (
    RoleEnricher,
    ArchiveHintEnricher,
    TimeEntryHintEnricher,
    CustomerSearchHintEnricher,
    EmployeeSearchHintEnricher,
    PaginationHintEnricher,
    CustomerProjectsHintEnricher,
    SearchResultExtractionHintEnricher,
    ProjectNameNormalizationHintEnricher,
    WorkloadHintEnricher,
    SkillSearchStrategyHintEnricher,
    EmployeeNameResolutionHintEnricher,
    SkillComparisonHintEnricher,
    QuerySubjectHintEnricher,
    TieBreakerHintEnricher,
    RecommendationQueryHintEnricher,
    TimeSummaryFallbackHintEnricher,
    ProjectTeamNameResolutionHintEnricher,
)

__all__ = [
    'ProjectRankingEnricher',
    'ProjectOverlapAnalyzer',
    'ProjectSearchEnricher',
    'WikiHintEnricher',
    'BonusPolicyEnricher',
    'EfficiencyHintEnricher',
    'RoleEnricher',
    'ArchiveHintEnricher',
    'TimeEntryHintEnricher',
    'CustomerSearchHintEnricher',
    'EmployeeSearchHintEnricher',
    'PaginationHintEnricher',
    'CustomerProjectsHintEnricher',
    'SearchResultExtractionHintEnricher',
    'ProjectNameNormalizationHintEnricher',
    'WorkloadHintEnricher',
    'SkillSearchStrategyHintEnricher',
    'EmployeeNameResolutionHintEnricher',
    'SkillComparisonHintEnricher',
    'QuerySubjectHintEnricher',
    'TieBreakerHintEnricher',
    'RecommendationQueryHintEnricher',
    'TimeSummaryFallbackHintEnricher',
    'ProjectTeamNameResolutionHintEnricher',
]
