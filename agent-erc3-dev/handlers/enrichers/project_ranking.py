"""
Project ranking enricher.

Ranks project search results by match quality to help agent disambiguate.
"""
from typing import Any, List, Tuple, Optional


class ProjectRankingEnricher:
    """
    Ranks project search results by match quality.

    Match levels (descending priority):
    - EXACT (100): Project name exactly matches query
    - STRONG (85-90): Query is prefix of name or appears as phrase within name
    - GOOD (70): All query words present in name
    - PARTIAL (30-60): Some query words match
    - WEAK (10): Minimal or no match
    """

    # Score thresholds
    SCORE_EXACT = 100
    SCORE_STRONG_PREFIX = 90
    SCORE_STRONG_PHRASE = 85
    SCORE_GOOD = 70
    SCORE_PARTIAL_BASE = 30
    SCORE_WEAK = 10

    # Clear winner threshold: top score must be >= 85 and >= 30 points above second
    CLEAR_WINNER_MIN_SCORE = 85
    CLEAR_WINNER_GAP = 30

    def enrich(self, projects: List[Any], query: str) -> Optional[str]:
        """
        Generate ranking hint for project search results.

        Args:
            projects: List of project objects with 'name' and 'id' attributes
            query: Original search query

        Returns:
            Hint string or None if not applicable (single result or no query)
        """
        if not query or len(projects) <= 1:
            return None

        ranked = self._rank_projects(projects, query)
        return self._format_hint(ranked, query)

    def _rank_projects(
        self, projects: List[Any], query: str
    ) -> List[Tuple[float, str, str, str]]:
        """
        Rank projects by match quality.

        Returns:
            List of (score, rank_label, project_name, project_id) tuples, sorted by score desc
        """
        query_lower = query.lower().strip()
        query_words = set(query_lower.split())

        ranked_results = []
        for project in projects:
            proj_name = (getattr(project, 'name', '') or '').lower().strip()
            proj_id = getattr(project, 'id', '')
            name_words = set(proj_name.split())

            score, rank = self._calculate_score(
                proj_name, name_words, query_lower, query_words
            )
            ranked_results.append((score, rank, proj_name, proj_id))

        # Sort by score descending
        ranked_results.sort(key=lambda x: -x[0])
        return ranked_results

    def _calculate_score(
        self,
        proj_name: str,
        name_words: set,
        query_lower: str,
        query_words: set
    ) -> Tuple[float, str]:
        """
        Calculate match score and rank label for a project.

        Returns:
            Tuple of (score, rank_label)
        """
        # EXACT: Full string match
        if proj_name == query_lower:
            return self.SCORE_EXACT, "EXACT"

        # STRONG: Query is prefix of project name
        if proj_name.startswith(query_lower + ' ') or proj_name.startswith(query_lower + ':'):
            return self.SCORE_STRONG_PREFIX, "STRONG"

        # STRONG: Query phrase appears within name
        if f' {query_lower} ' in f' {proj_name} ':
            return self.SCORE_STRONG_PHRASE, "STRONG"

        # GOOD: All query words present in name
        if query_words <= name_words:
            return self.SCORE_GOOD, "GOOD"

        # PARTIAL: Some words match
        overlap = len(query_words & name_words)
        if overlap > 0:
            # Scale score based on overlap percentage
            score = self.SCORE_PARTIAL_BASE + (overlap / len(query_words)) * 30
            return score, "PARTIAL"

        # WEAK: No meaningful match
        return self.SCORE_WEAK, "WEAK"

    def _format_hint(
        self, ranked: List[Tuple[float, str, str, str]], query: str
    ) -> str:
        """
        Format ranking results into a hint string.

        Args:
            ranked: List of (score, rank, name, id) tuples
            query: Original search query

        Returns:
            Formatted hint string
        """
        # Build ranking lines
        ranking_lines = []
        for score, rank, name, pid in ranked:
            ranking_lines.append(f"  [{rank}] {name} ({pid})")

        # Check for clear winner
        top_score = ranked[0][0]
        second_score = ranked[1][0] if len(ranked) > 1 else 0
        clear_winner = (
            top_score >= self.CLEAR_WINNER_MIN_SCORE and
            (top_score - second_score) >= self.CLEAR_WINNER_GAP
        )

        if clear_winner:
            _, top_rank, top_name, top_id = ranked[0]
            return (
                f"\n💡 SEARCH RANKING for query '{query}':\n"
                + "\n".join(ranking_lines)
                + f"\n\n✅ CLEAR MATCH: '{top_name}' ({top_id}) is a {top_rank} match. "
                f"Other results are significantly weaker. Proceed with this project."
            )
        else:
            # Multiple strong matches or no clear winner - genuinely ambiguous
            return (
                f"\n📊 SEARCH RANKING for query '{query}':\n"
                + "\n".join(ranking_lines)
                + f"\n\nUse this ranking to determine the best match. "
                f"EXACT/STRONG matches contain the full query phrase; PARTIAL matches only share some words."
            )
