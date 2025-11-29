import os
import time
import random
from typing import Any, List, Optional, Dict, Iterator
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, AIMessage, SystemMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatResult, ChatGeneration
from pydantic import Field, PrivateAttr

# We need a client wrapper similar to the original solution.
# The original used `from gonka_openai import GonkaOpenAI`.
# Since we don't have that package installed in our venv yet, and it seems to be a custom wrapper
# in the other project's venv, let's look at how to replicate or install it.
# For now, let's assume we can use standard OpenAI client but configured for Gonka.

from gonka_openai import GonkaOpenAI
from utils import get_available_nodes, GENESIS_NODES, CLI_RED, CLI_YELLOW, CLI_CYAN, CLI_CLR

class GonkaChatModel(BaseChatModel):
    """
    LangChain ChatModel wrapper for Gonka Network with automatic node failover.
    """
    model_name: str = Field(alias="model")
    gonka_private_key: str = Field(default_factory=lambda: os.getenv("GONKA_PRIVATE_KEY"))
    max_retries_per_node: int = 3
    max_node_switches: int = 10
    request_timeout: int = 10
    
    _client: Optional[GonkaOpenAI] = PrivateAttr(default=None)
    _current_node: Optional[str] = PrivateAttr(default=None)
    _tried_nodes: set = PrivateAttr(default_factory=set)
    
    # Class-level cache for persistence across instances
    _last_successful_node: Optional[str] = None

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[Any] = None,
        **kwargs: Any,
    ) -> ChatResult:
        
        # Convert LangChain messages to OpenAI format
        openai_messages = self._convert_messages(messages)
        
        # Ensure we have a client
        if self._client is None:
            self._connect_initial()

        for node_attempt in range(self.max_node_switches):
            # Try current node with retries
            try:
                response = self._call_with_retry(openai_messages, stop, **kwargs)
                
                # Extract content and usage
                message_content = response.choices[0].message.content
                usage = getattr(response, "usage", None)
                
                # Map usage to dict
                usage_metadata = {}
                if usage:
                    # Handle both object (Pydantic) and dict access
                    if isinstance(usage, dict):
                        usage_metadata = {
                            "prompt_tokens": usage.get("prompt_tokens", 0),
                            "completion_tokens": usage.get("completion_tokens", 0),
                            "total_tokens": usage.get("total_tokens", 0)
                        }
                    else:
                        usage_metadata = {
                            "prompt_tokens": getattr(usage, "prompt_tokens", 0),
                            "completion_tokens": getattr(usage, "completion_tokens", 0),
                            "total_tokens": getattr(usage, "total_tokens", 0)
                        }
                
                # Fallback estimation if usage is missing or empty
                if not usage_metadata or usage_metadata.get("total_tokens", 0) == 0:
                    # Estimate: 1 token ~= 4 chars
                    est_completion = len(message_content) // 4 if message_content else 0
                    # Rough prompt estimation from messages
                    est_prompt = sum(len(m.get('content', '')) for m in openai_messages) // 4
                    
                    usage_metadata = {
                        "prompt_tokens": est_prompt,
                        "completion_tokens": est_completion,
                        "total_tokens": est_prompt + est_completion,
                        "estimated": True
                    }

                return ChatResult(
                    generations=[ChatGeneration(
                        message=AIMessage(content=message_content),
                    )],
                    llm_output={"token_usage": usage_metadata, "model_name": self.model_name}
                )

            except Exception as e:
                print(f"{CLI_YELLOW}⚠ Node {self._current_node} failed: {e}{CLI_CLR}")
                # Switch node
                if not self._switch_node():
                    raise e
        
        raise Exception("All Gonka nodes failed.")

    def _call_with_retry(self, messages, stop, **kwargs):
        last_error = None
        for attempt in range(self.max_retries_per_node):
            try:
                # Standard OpenAI call
                return self._client.chat.completions.create(
                    model=self.model_name,
                    messages=messages,
                    stop=stop,
                    temperature=kwargs.get("temperature", 0.0),
                    timeout=self.request_timeout
                )
            except Exception as e:
                # Check for critical connection errors that should trigger immediate node switch
                # rather than retrying the same node
                error_str = str(e).lower()
                critical_errors = [
                    "connection aborted", 
                    "remote end closed", 
                    "connection refused", 
                    "connecttimeouterror",
                    "remotedisconnected"
                ]
                
                if any(ce in error_str for ce in critical_errors):
                    print(f"{CLI_YELLOW}⚠ Critical network error on node {self._current_node}: {e}{CLI_CLR}")
                    raise e  # Re-raise to trigger _switch_node in the outer loop

                last_error = e
                print(f"{CLI_YELLOW}⚠ Retry {attempt+1}/{self.max_retries_per_node} on {self._current_node}: {e}{CLI_CLR}")
                wait_time = (attempt + 1) * 2
                if attempt < self.max_retries_per_node - 1:
                    time.sleep(wait_time)
        raise last_error

    def _connect_initial(self):
        fixed_node = os.getenv("GONKA_NODE_URL")
        if fixed_node:
            print(f"{CLI_CYAN}🔗 Using fixed node: {fixed_node}{CLI_CLR}")
            self._client = GonkaOpenAI(gonka_private_key=self.gonka_private_key, source_url=fixed_node)
            self._current_node = fixed_node
            return

        # Try to find a working node
        nodes = get_available_nodes()
        for node in nodes[:3]:
            try:
                print(f"{CLI_YELLOW}🔗 Connecting to: {node}{CLI_CLR}")
                self._client = GonkaOpenAI(gonka_private_key=self.gonka_private_key, source_url=node)
                self._current_node = node
                self._tried_nodes.add(node)
                return
            except Exception:
                continue
        
        # Fallback
        fallback = GENESIS_NODES[0]
        self._client = GonkaOpenAI(gonka_private_key=self.gonka_private_key, source_url=fallback)
        self._current_node = fallback
        self._tried_nodes.add(fallback)

    def _switch_node(self) -> bool:
        """Switch to a new node. Returns True if successful."""
        available_nodes = get_available_nodes()
        new_node = None
        
        # Find unused node
        for node in available_nodes:
            if node not in self._tried_nodes:
                new_node = node
                break
        
        if not new_node and available_nodes:
             new_node = random.choice(available_nodes)

        if not new_node:
            print(f"{CLI_RED}✗ No nodes available for failover{CLI_CLR}")
            return False

        print(f"{CLI_CYAN}🔄 Switching to node: {new_node}{CLI_CLR}")
        self._tried_nodes.add(new_node)
        try:
            self._client = GonkaOpenAI(gonka_private_key=self.gonka_private_key, source_url=new_node)
            self._current_node = new_node
            return True
        except Exception as e:
            print(f"{CLI_RED}✗ Failed to connect to {new_node}: {e}{CLI_CLR}")
            return False

    def _convert_messages(self, messages: List[BaseMessage]) -> List[Dict]:
        openai_msgs = []
        for m in messages:
            if isinstance(m, SystemMessage):
                openai_msgs.append({"role": "system", "content": m.content})
            elif isinstance(m, HumanMessage):
                openai_msgs.append({"role": "user", "content": m.content})
            elif isinstance(m, AIMessage):
                openai_msgs.append({"role": "assistant", "content": m.content})
            elif isinstance(m, ToolMessage):
                 openai_msgs.append({"role": "tool", "content": m.content, "tool_call_id": m.tool_call_id})
            else:
                openai_msgs.append({"role": "user", "content": m.content})
        return openai_msgs

    @property
    def _llm_type(self) -> str:
        return "gonka-chat-model"
