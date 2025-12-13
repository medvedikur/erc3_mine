"""
Test result evaluator.

Evaluates agent responses against expected outcomes,
similar to the official benchmark evaluation.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Set

from .task_builder import TestScenario, ExpectedResult, AgentLink


@dataclass
class TestResult:
    """Result of a single test evaluation."""
    spec_id: str
    score: float  # 0.0 to 1.0
    passed: bool
    logs: List[str] = field(default_factory=list)
    actual_outcome: Optional[str] = None
    expected_outcome: Optional[str] = None
    actual_links: List[Dict] = field(default_factory=list)
    expected_links: List[Dict] = field(default_factory=list)
    error: Optional[str] = None
    turns_used: int = 0
    api_calls: List[str] = field(default_factory=list)
    context_results: List[Dict] = field(default_factory=list)  # All ctx.results from execution

    def __str__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        lines = [f"[{status}] {self.spec_id}: {self.score:.1f}"]
        for log in self.logs:
            lines.append(f"  {log}")
        if self.error:
            lines.append(f"  ERROR: {self.error}")

        # Include context_results (hints, guards, enrichments) for all tests (useful for analysis)
        if self.context_results:
            lines.append("")
            lines.append("=" * 50)
            lines.append("CONTEXT RESULTS (hints/guards/enrichments):")
            lines.append("=" * 50)
            for ctx_result in self.context_results:
                action = ctx_result.get('action', 'unknown')
                lines.append(f"\n[{action}]")
                for result in ctx_result.get('results', []):
                    # Format multi-line results nicely
                    result_str = str(result)
                    if '\n' in result_str:
                        lines.append("  " + result_str.replace('\n', '\n  '))
                    else:
                        lines.append(f"  {result_str}")

        return "\n".join(lines)


class TestEvaluator:
    """
    Evaluates agent responses against expected results.

    Scoring logic (mimics benchmark):
    - Outcome match: 50%
    - Links match: 50%

    For links:
    - All expected links must be present
    - Extra links are allowed (no penalty)
    """

    def __init__(self, strict_links: bool = False):
        """
        Initialize evaluator.

        Args:
            strict_links: If True, extra links cause point deduction
        """
        self.strict_links = strict_links

    def evaluate(
        self,
        scenario: TestScenario,
        agent_response: Optional[Dict[str, Any]],
        api_calls: List[Any] = None,
        turns_used: int = 0,
        error: Optional[str] = None,
    ) -> TestResult:
        """
        Evaluate agent response against expected result.

        Args:
            scenario: Test scenario with expected results
            agent_response: Agent's final response (outcome, message, links)
            api_calls: List of API calls made by agent
            turns_used: Number of turns agent used
            error: Error message if agent failed

        Returns:
            TestResult with score and evaluation details
        """
        result = TestResult(
            spec_id=scenario.spec_id,
            score=0.0,
            passed=False,
            expected_outcome=scenario.expected.outcome,
            expected_links=[l.to_dict() for l in scenario.expected.links],
            turns_used=turns_used,
            api_calls=[type(c.request).__name__ if hasattr(c, 'request') else str(c) for c in (api_calls or [])],
        )

        # If error occurred, test fails
        if error:
            result.error = error
            result.logs.append(f"FAIL: Agent error - {error}")
            return result

        # If no response, test fails
        if not agent_response:
            result.error = "No response from agent"
            result.logs.append("FAIL: Agent did not provide a response")
            return result

        # Extract actual values
        result.actual_outcome = agent_response.get('outcome', '')
        result.actual_links = agent_response.get('links', [])

        # Normalize links for comparison
        actual_link_set = self._normalize_links(result.actual_links)
        expected_link_set = self._normalize_links(result.expected_links)

        # =====================================================================
        # SCORING - BINARY (either 1.0 pass or 0.0 fail)
        # All criteria must pass:
        # - Outcome matches expected
        # - All expected links present
        # - Message contains required text (if specified)
        # - Message does NOT contain forbidden text (if specified)
        # =====================================================================

        all_checks_pass = True

        # 1. Check outcome
        outcome_match = result.actual_outcome == result.expected_outcome
        if outcome_match:
            result.logs.append(f"OK: outcome '{result.actual_outcome}' matches expected")
        else:
            result.logs.append(
                f"FAIL: expected outcome '{result.expected_outcome}', "
                f"got '{result.actual_outcome}'"
            )
            all_checks_pass = False

        # 2. Check links
        missing_links = set()
        extra_links = set()

        if expected_link_set:
            missing_links = expected_link_set - actual_link_set
            extra_links = actual_link_set - expected_link_set

            if len(missing_links) == 0:
                result.logs.append(f"OK: all {len(expected_link_set)} expected links present")
            else:
                result.logs.append(
                    f"FAIL: missing {len(missing_links)} links: "
                    f"{[self._link_str(l) for l in missing_links]}"
                )
                all_checks_pass = False
        else:
            result.logs.append("OK: no links expected")

        if extra_links and self.strict_links:
            result.logs.append(f"INFO: {len(extra_links)} extra links (allowed)")

        # 3. Check message_contains (if specified)
        if scenario.expected.message_contains:
            message = agent_response.get('message', '')
            for expected_text in scenario.expected.message_contains:
                if expected_text.lower() in message.lower():
                    result.logs.append(f"OK: message contains '{expected_text}'")
                else:
                    result.logs.append(f"FAIL: message missing required text: '{expected_text}'")
                    all_checks_pass = False

        # 4. Check message_not_contains (if specified)
        if scenario.expected.message_not_contains:
            message = agent_response.get('message', '')
            for forbidden_text in scenario.expected.message_not_contains:
                if forbidden_text.lower() in message.lower():
                    result.logs.append(f"FAIL: message contains forbidden text: '{forbidden_text}'")
                    all_checks_pass = False
                else:
                    result.logs.append(f"OK: message does not contain '{forbidden_text}'")

        # 5. Custom validator (if specified)
        if scenario.custom_validator:
            try:
                if scenario.custom_validator(agent_response, api_calls or []):
                    result.logs.append("OK: custom validator passed")
                else:
                    result.logs.append("FAIL: custom validator failed")
                    all_checks_pass = False
            except Exception as e:
                result.logs.append(f"FAIL: custom validator error: {e}")
                all_checks_pass = False

        # Final score - BINARY: 1.0 or 0.0
        if all_checks_pass:
            result.score = 1.0
            result.passed = True
        else:
            result.score = 0.0
            result.passed = False

        return result

    def _normalize_links(self, links: List[Dict]) -> Set[tuple]:
        """Convert links to set of (kind, id) tuples for comparison."""
        normalized = set()
        for link in links:
            if isinstance(link, dict):
                kind = link.get('kind', link.get('Kind', ''))
                id_ = link.get('id', link.get('ID', ''))
                if kind and id_:
                    normalized.add((kind.lower(), id_.lower()))
            elif hasattr(link, 'kind') and hasattr(link, 'id'):
                normalized.add((link.kind.lower(), link.id.lower()))
        return normalized

    def _link_str(self, link_tuple: tuple) -> str:
        """Format link tuple as string."""
        return f"{link_tuple[0]}:{link_tuple[1]}"


@dataclass
class TestSuiteResult:
    """Result of running a test suite."""
    total: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    total_score: float = 0.0
    results: List[TestResult] = field(default_factory=list)

    @property
    def average_score(self) -> float:
        """Calculate average score across all tests."""
        if self.total == 0:
            return 0.0
        return self.total_score / self.total

    @property
    def pass_rate(self) -> float:
        """Calculate pass rate as percentage."""
        if self.total == 0:
            return 0.0
        return (self.passed / self.total) * 100

    def add_result(self, result: TestResult):
        """Add a test result to the suite."""
        self.results.append(result)
        self.total += 1
        self.total_score += result.score

        if result.error:
            self.errors += 1
        elif result.passed:
            self.passed += 1
        else:
            self.failed += 1

    def print_summary(self):
        """Print summary of test suite results."""
        print("\n" + "=" * 60)
        print("TEST SUITE RESULTS")
        print("=" * 60)
        print(f"  Total tests:    {self.total}")
        print(f"  Passed:         {self.passed}")
        print(f"  Failed:         {self.failed}")
        print(f"  Errors:         {self.errors}")
        print(f"  Average score:  {self.average_score:.2f}")
        print(f"  Pass rate:      {self.pass_rate:.1f}%")
        print("-" * 60)

        # Show failed tests
        if self.failed > 0 or self.errors > 0:
            print("\nFailed/Error tests:")
            for result in self.results:
                if not result.passed or result.error:
                    print(f"\n  {result.spec_id}:")
                    for log in result.logs:
                        print(f"    {log}")
                    if result.error:
                        print(f"    ERROR: {result.error}")

        print("=" * 60)
