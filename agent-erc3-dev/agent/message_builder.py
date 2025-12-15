"""
Message construction for agent conversation.

Handles building system messages, error messages, and feedback messages.
"""

from typing import List, Optional

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, BaseMessage

import config
from prompts import SGR_SYSTEM_PROMPT
from handlers import WikiManager


# Error message templates
JSON_ERROR_MSG = (
    "[SYSTEM ERROR]: Invalid JSON. Respond with ONLY valid JSON: "
    '{"thoughts": "...", "plan": [...], "action_queue": [...], "is_final": false}'
)

IS_FINAL_NO_RESPOND_MSG = """[SYSTEM ERROR]: You set is_final=true but didn't call 'respond' tool!

Add respond to action_queue:
{
  "action_queue": [{"tool": "respond", "args": {"outcome": "...", "message": "...", "links": [...]}}],
  "is_final": false
}"""

LOOP_DETECTED_MSG = """[SYSTEM ERROR]: Loop detected - same actions for 3 turns!

This usually means:
1. Feature doesn't exist -> respond 'none_unsupported'
2. Missing info -> respond 'none_clarification_needed'
3. Permissions issue -> respond 'denied_security'

STOP repeating and call 'respond' with appropriate outcome."""

NO_ACTIONS_MSG = """[SYSTEM ERROR]: NO ACTIONS EXECUTED!

Your action_queue may have had:
- Malformed JSON
- Unknown tool names
- Missing required fields

Please retry with correct syntax."""


class MessageBuilder:
    """
    Builds messages for the agent conversation.

    Handles:
    - Initial system message setup
    - Task context message
    - Error feedback messages
    - Action result feedback
    """

    def __init__(self, wiki_manager: WikiManager):
        """
        Initialize the message builder.

        Args:
            wiki_manager: WikiManager for context summary
        """
        self.wiki_manager = wiki_manager

    def build_initial_messages(self, task_text: str) -> List[BaseMessage]:
        """
        Build initial conversation messages.

        Args:
            task_text: The task text from benchmark

        Returns:
            List of initial messages (system + human)
        """
        # Add turn budget info to help agent plan efficiently
        max_turns = config.MAX_TURNS_PER_TASK
        turn_budget_hint = (
            f"\n\n## ⏱️ TURN BUDGET & EFFICIENCY\n"
            f"You have **{max_turns} turns** to complete this task. Plan efficiently!\n\n"
            f"### CRITICAL: Parallel Execution\n"
            f"- **action_queue accepts MULTIPLE actions** — they ALL execute in ONE turn!\n"
            f"- Put 10-30 `projects_get` or `employees_get` calls in ONE action_queue\n"
            f"- Example: instead of 20 sequential calls (20 turns), batch them (1 turn)\n\n"
            f"### CRITICAL: Batch APIs\n"
            f"- `time_summary_employee(employees=[\"id1\", \"id2\", ...])` — pass ALL IDs in ONE call!\n"
            f"- Returns aggregated data for ALL employees at once\n\n"
            f"### For 'busiest/most X' queries:\n"
            f"1. Get employee list with filter (department=X) — stop after 2-3 pages\n"
            f"2. Use `time_summary_employee(employees=[all IDs])` to get hours in ONE call\n"
            f"3. OR batch `projects_get` calls in ONE action_queue to get time_slice\n"
            f"4. Analyze and respond — DON'T paginate through ALL records!\n\n"
            f"### Filters\n"
            f"- Use `department=`, `location=`, `member=`, `owner=` to narrow searches"
        )

        system_prompt_with_budget = SGR_SYSTEM_PROMPT + turn_budget_hint

        return [
            SystemMessage(content=system_prompt_with_budget),
            HumanMessage(content=f"TASK: {task_text}\n\nContext: {self.wiki_manager.get_context_summary()}")
        ]

    def build_json_error_message(self) -> HumanMessage:
        """Build message for JSON parse error."""
        return HumanMessage(content=JSON_ERROR_MSG)

    def build_is_final_error_message(self) -> HumanMessage:
        """Build message for is_final without respond."""
        return HumanMessage(content=IS_FINAL_NO_RESPOND_MSG)

    def build_loop_detected_message(self) -> HumanMessage:
        """Build message for loop detection."""
        return HumanMessage(content=LOOP_DETECTED_MSG)

    def build_no_actions_message(self) -> HumanMessage:
        """Build message for no actions executed."""
        return HumanMessage(content=NO_ACTIONS_MSG)

    def build_malformed_actions_message(
        self,
        malformed_count: int,
        mutation_tools: Optional[List[str]] = None
    ) -> HumanMessage:
        """
        Build message for malformed actions.

        Args:
            malformed_count: Number of malformed actions
            mutation_tools: List of mutation tool names that were malformed

        Returns:
            HumanMessage with error details
        """
        mutation_warning = ""
        if mutation_tools:
            mutation_warning = f"\n\nCRITICAL: Malformed mutation(s): {', '.join(mutation_tools)}. NOT executed!"

        content = f"""[SYSTEM ERROR]: {malformed_count} action(s) were malformed.

Each action MUST have: {{"tool": "tool_name", "args": {{...}}}}{mutation_warning}

The malformed actions were NOT executed. Please retry."""
        return HumanMessage(content=content)

    def build_results_message(
        self,
        results: List[str],
        current_turn: int = None,
        max_turns: int = None
    ) -> HumanMessage:
        """
        Build feedback message from action results.

        Args:
            results: List of action result strings
            current_turn: Current turn number (0-indexed)
            max_turns: Maximum turns allowed

        Returns:
            HumanMessage with execution log
        """
        if not results:
            return self.build_no_actions_message()

        feedback = "\n---\n".join(results)

        # Add turn budget reminder if running low
        turn_header = ""
        if current_turn is not None and max_turns is not None:
            remaining = max_turns - current_turn - 1
            if remaining <= 3:
                turn_header = (
                    f"🛑 [TURN {current_turn + 1}/{max_turns}] "
                    f"ONLY {remaining} TURNS LEFT - RESPOND SOON!\n\n"
                )
            elif remaining <= 5:
                turn_header = (
                    f"⚠️ [TURN {current_turn + 1}/{max_turns}] "
                    f"{remaining} turns remaining - start wrapping up\n\n"
                )

        return HumanMessage(content=f"{turn_header}[EXECUTION LOG]\n{feedback}")
