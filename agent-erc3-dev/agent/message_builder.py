"""
Message construction for agent conversation.

Handles building system messages, error messages, and feedback messages.
"""

from typing import List, Optional

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, BaseMessage

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
        return [
            SystemMessage(content=SGR_SYSTEM_PROMPT),
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

    def build_results_message(self, results: List[str]) -> HumanMessage:
        """
        Build feedback message from action results.

        Args:
            results: List of action result strings

        Returns:
            HumanMessage with execution log
        """
        if results:
            feedback = "\n---\n".join(results)
            return HumanMessage(content=f"[EXECUTION LOG]\n{feedback}")
        else:
            return self.build_no_actions_message()
