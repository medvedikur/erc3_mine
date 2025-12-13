"""
Project ranking enricher.

Ranks project search results by match quality to help agent disambiguate.
"""
import re
from typing import Any, List, Tuple, Optional


class ProjectRankingEnricher:
    """
    Ranks project search results by match quality.

    Match levels (descending priority):
    - EXACT (100): Project name exactly matches query
    - STRONG (85-95): Query is prefix/phrase in name, or consecutive word sequence matches
    - GOOD (70-80): All query words present in name
    - PARTIAL (30-60): Some query words match
    - WEAK (10): Minimal or no match
    """

    # Score thresholds
    SCORE_EXACT = 100
    SCORE_STRONG_PREFIX = 95
    SCORE_STRONG_SEQUENCE = 90  # Consecutive words from query appear together
    SCORE_STRONG_PHRASE = 85
    SCORE_GOOD = 70
    SCORE_PARTIAL_BASE = 30
    SCORE_WEAK = 10

    # Clear winner threshold: top score must be >= 70 and >= 15 points above second
    # Lowered from 85/30 to allow "Line 3" vs "Packaging Line" differentiation
    CLEAR_WINNER_MIN_SCORE = 70
    CLEAR_WINNER_GAP = 15

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
        query_words = query_lower.split()  # Keep as list for sequence matching
        query_words_set = set(query_words)

        ranked_results = []
        for project in projects:
            proj_name = (getattr(project, 'name', '') or '').lower().strip()
            proj_id = getattr(project, 'id', '')
            name_words = proj_name.split()
            name_words_set = set(name_words)

            score, rank = self._calculate_score(
                proj_name, name_words, name_words_set,
                query_lower, query_words, query_words_set
            )
            ranked_results.append((score, rank, proj_name, proj_id))

        # Sort by score descending
        ranked_results.sort(key=lambda x: -x[0])
        return ranked_results

    def _calculate_score(
        self,
        proj_name: str,
        name_words: List[str],
        name_words_set: set,
        query_lower: str,
        query_words: List[str],
        query_words_set: set
    ) -> Tuple[float, str]:
        """
        Calculate match score and rank label for a project.

        Returns:
            Tuple of (score, rank_label)
        """
        # EXACT: Full string match
        if proj_name == query_lower:
            return self.SCORE_EXACT, "EXACT"

        # STRONG: Query is prefix of project name (e.g., "Line 3" -> "Line 3 Defect Detection")
        if proj_name.startswith(query_lower + ' ') or proj_name.startswith(query_lower + ':'):
            return self.SCORE_STRONG_PREFIX, "STRONG"

        # STRONG: Query phrase appears within name
        if f' {query_lower} ' in f' {proj_name} ':
            return self.SCORE_STRONG_PHRASE, "STRONG"

        # Check for consecutive word sequence match
        # "Line 3" in "Line 3 Defect Detection" is strong
        # "Line CV" in "Packaging Line CV" is weak (not consecutive in query)
        seq_score = self._check_consecutive_sequence(query_words, name_words)
        if seq_score >= 2:  # At least 2 consecutive query words found together
            # Score based on how much of the query was matched consecutively
            consecutive_ratio = seq_score / len(query_words)
            if consecutive_ratio >= 0.66:  # 2/3 or more consecutive
                return self.SCORE_STRONG_SEQUENCE, "STRONG"
            elif consecutive_ratio >= 0.5:
                return self.SCORE_GOOD + 5, "GOOD"

        # GOOD: All query words present in name (but not necessarily consecutive)
        if query_words_set <= name_words_set:
            return self.SCORE_GOOD, "GOOD"

        # PARTIAL: Some words match
        overlap = len(query_words_set & name_words_set)
        if overlap > 0:
            # Scale score based on overlap percentage
            overlap_ratio = overlap / len(query_words_set)
            score = self.SCORE_PARTIAL_BASE + overlap_ratio * 30
            return score, "PARTIAL"

        # WEAK: No meaningful match
        return self.SCORE_WEAK, "WEAK"

    def _check_consecutive_sequence(
        self, query_words: List[str], name_words: List[str]
    ) -> int:
        """
        Check for longest consecutive sequence of query words in name.

        "Line 3" in ["Line", "3", "Defect", "Detection"] -> 2 (both consecutive)
        "Line 3 CV" in ["Packaging", "Line", "CV"] -> 1 (only "Line" alone, CV not next to Line)

        Returns:
            Length of longest consecutive query subsequence found
        """
        if len(query_words) < 2:
            return len(query_words) if query_words[0] in name_words else 0

        max_consecutive = 0

        # Try to find consecutive words from query in name
        for start_idx in range(len(query_words)):
            # Try starting from each position in query
            consecutive = 0
            name_idx = 0

            for q_idx in range(start_idx, len(query_words)):
                q_word = query_words[q_idx]
                # Find this word in name starting from current position
                found = False
                for n_idx in range(name_idx, len(name_words)):
                    if name_words[n_idx] == q_word:
                        if consecutive == 0 or n_idx == name_idx:
                            # First match or immediately following previous match
                            consecutive += 1
                            name_idx = n_idx + 1
                            found = True
                            break
                        else:
                            # Gap in name - sequence broken
                            break

                if not found:
                    break

            max_consecutive = max(max_consecutive, consecutive)

        return max_consecutive

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
