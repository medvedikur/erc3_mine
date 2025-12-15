"""
Criteria Guards - detect when agent adds criteria not present in the task.

Guards:
- AddedCriteriaGuard: Warns when agent's response uses criteria not mentioned in task
"""
import re
from ..base import ResponseGuard, get_task_text
from ...base import ToolContext


class AddedCriteriaGuard(ResponseGuard):
    """
    Detects when agent adds selection criteria that weren't in the original task.

    Problem: Agent hallucinates additional criteria when making decisions.
    Example: Task asks for "most skilled in solventborne" but agent responds
    with "best combination of skill AND willingness to travel".

    Solution: Check if agent's reasoning mentions criteria keywords not in task.
    Soft hint only - agent might have valid reasons from wiki policies.
    """

    target_outcomes = {"ok_answer"}

    # Keywords that indicate selection criteria in agent's response
    CRITERIA_KEYWORDS = {
        # Travel/mobility
        'travel': ['travel', 'traveling', 'willingness to travel', 'will_travel', 'mobile', 'mobility'],
        # Location preferences
        'location': ['location', 'barcelona', 'vienna', 'munich', 'city', 'office', 'site', 'headquarter'],
        # Salary/cost
        'salary': ['salary', 'cost', 'expensive', 'cheap', 'budget', 'compensation', 'pay'],
        # Seniority/experience
        'seniority': ['seniority', 'senior', 'junior', 'years of experience', 'tenure', 'experience level'],
        # Availability
        'availability': ['availability', 'available', 'busy', 'workload', 'utilization', 'capacity'],
        # Training specific
        'training': ['training', 'trainee', 'course', 'certification', 'workshop'],
    }

    # Skip patterns - if task contains these, we shouldn't flag
    TASK_EXPLICIT_PATTERNS = [
        # If task mentions "for training" or "to send to Barcelona", criteria is valid
        r'for\s+training',
        r'send\s+to\s+\w+',
        r'to\s+attend',
        r'conference\s+in',
        r'workshop\s+in',
    ]

    def __init__(self):
        self._skip_re = re.compile('|'.join(self.TASK_EXPLICIT_PATTERNS), re.IGNORECASE)
        # Build reverse lookup: word -> category
        self._word_to_category = {}
        for category, words in self.CRITERIA_KEYWORDS.items():
            for word in words:
                self._word_to_category[word.lower()] = category

    def _check(self, ctx: ToolContext, outcome: str) -> None:
        task_text = get_task_text(ctx)
        if not task_text:
            return

        # Get agent's response message and thoughts
        message = (ctx.model.message or "").lower()
        thoughts = ctx.shared.get('last_thoughts', '').lower()
        combined_response = f"{message} {thoughts}"

        task_lower = task_text.lower()

        # Skip if task has explicit training/travel context
        if self._skip_re.search(task_lower):
            return

        # Find criteria categories mentioned in response but NOT in task
        added_criteria = self._find_added_criteria(task_lower, combined_response)

        if added_criteria:
            criteria_list = ', '.join(sorted(added_criteria))
            self._soft_hint(
                ctx,
                f"AddedCriteriaGuard: Agent added criteria not in task: {criteria_list}",
                (
                    f"\n⚠️ CRITERIA WARNING: Your response uses criteria ({criteria_list}) "
                    f"that were NOT mentioned in the original task.\n\n"
                    f"**Task asked for**: {task_text[:200]}...\n\n"
                    f"**IMPORTANT**: Unless wiki policies REQUIRE these criteria, "
                    f"you should answer based ONLY on what the task asks for.\n\n"
                    f"If the task says 'most skilled in X', respond with the person "
                    f"who has the highest skill level in X - don't add travel willingness, "
                    f"salary, or other factors unless the task explicitly mentions them.\n\n"
                    f"**ACTION**: If you added criteria that the task didn't ask for, "
                    f"reconsider your answer using ONLY the requested criteria."
                )
            )

    def _find_added_criteria(self, task_text: str, response_text: str) -> set:
        """
        Find criteria categories that appear in response but not in task.

        Returns set of category names (e.g., {'travel', 'salary'}).
        """
        # Find categories mentioned in task
        task_categories = set()
        for word, category in self._word_to_category.items():
            if word in task_text:
                task_categories.add(category)

        # Find categories mentioned in response
        response_categories = set()
        for word, category in self._word_to_category.items():
            if word in response_text:
                response_categories.add(category)

        # Return categories in response but NOT in task
        return response_categories - task_categories
