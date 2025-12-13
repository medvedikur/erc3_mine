"""
LLM invocation and response handling.

Handles calling the LLM, parsing responses, and tracking usage.
"""

import time
from typing import Optional, Tuple, List, Any

from langchain_core.messages import BaseMessage

from erc3 import TaskInfo, ERC3
from stats import SessionStats
from utils import CLI_RED, CLI_CLR

from .parsing import OpenAIUsage


class LLMInvoker:
    """
    Handles LLM invocation and response processing.

    Responsibilities:
    - Invoke LLM with messages
    - Extract and track token usage
    - Log LLM calls to ERC3 API
    - Handle errors gracefully
    """

    def __init__(
        self,
        llm: Any,
        api: ERC3,
        task: TaskInfo,
        model_name: str,
        cost_model_id: str,
        stats: Optional[SessionStats] = None,
    ):
        """
        Initialize the LLM invoker.

        Args:
            llm: LangChain LLM instance
            api: ERC3 API client
            task: Current task info
            model_name: Model name for logging
            cost_model_id: Model ID for cost calculation
            stats: Optional session statistics tracker
        """
        self.llm = llm
        self.api = api
        self.task = task
        self.model_name = model_name
        self.cost_model_id = cost_model_id
        self.stats = stats

    def invoke(
        self,
        messages: List[BaseMessage]
    ) -> Tuple[Optional[str], Optional[OpenAIUsage]]:
        """
        Invoke the LLM with messages.

        Args:
            messages: List of conversation messages

        Returns:
            Tuple of (raw_content, usage) or (None, None) on failure
        """
        started = time.time()

        try:
            result = self.llm.generate([messages])
            generation = result.generations[0][0]
            llm_output = result.llm_output or {}

            raw_content = generation.text
            usage = llm_output.get("token_usage", {})

            # Fallback if usage is missing
            if not usage or usage.get("completion_tokens", 0) == 0:
                usage = self._estimate_usage(messages, raw_content)

            usage_obj = OpenAIUsage(
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                total_tokens=usage.get("total_tokens", 0)
            )

            # Track stats
            if self.stats:
                self.stats.add_llm_usage(
                    self.cost_model_id,
                    usage_obj,
                    task_id=self.task.task_id
                )

            # Log to ERC3
            self.api.log_llm(
                task_id=self.task.task_id,
                completion=raw_content,
                model=self.model_name,
                duration_sec=time.time() - started,
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                cached_prompt_tokens=0
            )

            return raw_content, usage_obj

        except Exception as e:
            print(f"{CLI_RED}LLM call failed: {e}{CLI_CLR}")
            return None, None

    def _estimate_usage(
        self,
        messages: List[BaseMessage],
        raw_content: str
    ) -> dict:
        """
        Estimate token usage when not provided by LLM.

        Args:
            messages: Input messages
            raw_content: LLM response content

        Returns:
            Dict with estimated token counts
        """
        est_completion = len(raw_content) // 4
        est_prompt = sum(len(m.content) for m in messages) // 4
        return {
            "prompt_tokens": est_prompt,
            "completion_tokens": est_completion,
            "total_tokens": est_prompt + est_completion
        }
