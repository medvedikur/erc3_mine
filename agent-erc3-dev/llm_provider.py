"""
LLM Provider Module - Unified interface for multiple LLM backends.

Supports:
- Gonka Network (decentralized inference with automatic node failover)
- OpenRouter (commercial API with multiple model providers)
"""

import os
import time
import random
from typing import Any, List, Optional, Dict
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, AIMessage, SystemMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatResult, ChatGeneration
from pydantic import Field, PrivateAttr

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
    request_timeout: int = 60
    
    _client: Optional[GonkaOpenAI] = PrivateAttr(default=None)
    _current_node: Optional[str] = PrivateAttr(default=None)
    _tried_nodes: Optional[set] = PrivateAttr(default=None)
    
    # Class-level cache for persistence across instances
    _last_successful_node: Optional[str] = None
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._tried_nodes = set()  # Initialize here to avoid Pydantic issues

    def _extract_hint_url(self, error_msg: str) -> Optional[str]:
        """Extract URL from 'Try another TA from <url>' error message"""
        if "Try another TA from" in error_msg:
            try:
                import re
                match = re.search(r'(http://[^\s]+/participants)', error_msg)
                if match:
                    full_url = match.group(1)
                    return full_url.split("/v1/")[0]
            except Exception:
                pass
        return None

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[Any] = None,
        **kwargs: Any,
    ) -> ChatResult:
        
        openai_messages = self._convert_messages(messages)
        
        if self._client is None:
            self._connect_initial()

        for node_attempt in range(self.max_node_switches):
            try:
                response = self._call_with_retry(openai_messages, stop, **kwargs)
                
                if self._current_node:
                    GonkaChatModel._last_successful_node = self._current_node

                message_content = response.choices[0].message.content
                usage = getattr(response, "usage", None)
                
                usage_metadata = {}
                if usage:
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
                
                if not usage_metadata or usage_metadata.get("total_tokens", 0) == 0:
                    est_completion = len(message_content) // 4 if message_content else 0
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
                error_str = str(e)
                print(f"{CLI_YELLOW}⚠ Node {self._current_node} failed: {e}{CLI_CLR}")
                
                hint_node = self._extract_hint_url(error_str)
                if hint_node:
                    print(f"{CLI_CYAN}💡 Found hint node in error: {hint_node}{CLI_CLR}")
                
                if not self._switch_node(hint_node=hint_node):
                    raise e
        
        raise Exception("All Gonka nodes failed.")

    def _call_with_retry(self, messages, stop, **kwargs):
        last_error = None
        for attempt in range(self.max_retries_per_node):
            try:
                return self._client.chat.completions.create(
                    model=self.model_name,
                    messages=messages,
                    stop=stop,
                    temperature=kwargs.get("temperature", 0.0),
                    timeout=self.request_timeout
                )
            except Exception as e:
                error_str = str(e).lower()
                critical_errors = [
                    "connection aborted", 
                    "remote end closed", 
                    "connection refused", 
                    "connecttimeouterror",
                    "remotedisconnected",
                    "transfer agent capacity reached",
                    "429",
                    "signature is too old",
                    "signature is in the future",
                    "unable to validate request",
                    "invalid signature",
                    "request timed out",
                    "read timed out"
                ]
                
                if any(ce in error_str for ce in critical_errors):
                    print(f"{CLI_YELLOW}⚠ Critical error on node {self._current_node}: {e}{CLI_CLR}")
                    if "signature is too old" in error_str or "invalid signature" in error_str:
                        print(f"{CLI_RED}⚠ System clock is behind! Run: sudo sntp -sS time.apple.com{CLI_CLR}")
                    if "signature is in the future" in error_str:
                        print(f"{CLI_RED}⚠ System clock is ahead! Run: sudo sntp -sS time.apple.com{CLI_CLR}")
                    raise e

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

        if GonkaChatModel._last_successful_node:
            node = GonkaChatModel._last_successful_node
            try:
                print(f"{CLI_CYAN}🔗 Reusing last successful node: {node}{CLI_CLR}")
                self._client = GonkaOpenAI(gonka_private_key=self.gonka_private_key, source_url=node)
                self._current_node = node
                self._tried_nodes.add(node)
                return
            except Exception as e:
                print(f"{CLI_YELLOW}⚠ Last successful node {node} failed: {e}{CLI_CLR}")

        nodes = get_available_nodes()
        for node in nodes[:3]:
            if node in self._tried_nodes:
                continue
            try:
                print(f"{CLI_YELLOW}🔗 Connecting to: {node}{CLI_CLR}")
                self._client = GonkaOpenAI(gonka_private_key=self.gonka_private_key, source_url=node)
                self._current_node = node
                self._tried_nodes.add(node)
                return
            except Exception:
                continue
        
        fallback = GENESIS_NODES[0]
        self._client = GonkaOpenAI(gonka_private_key=self.gonka_private_key, source_url=fallback)
        self._current_node = fallback
        self._tried_nodes.add(fallback)

    def _switch_node(self, hint_node: str = None) -> bool:
        """Switch to a new node. Returns True if successful."""
        
        if hint_node:
            print(f"{CLI_CYAN}🔄 Fetching fresh nodes from hint: {hint_node}{CLI_CLR}")
            from utils import fetch_active_nodes
            fresh_nodes = fetch_active_nodes(source_node=hint_node)
            if fresh_nodes:
                for node in fresh_nodes:
                    if node not in self._tried_nodes:
                        print(f"{CLI_CYAN}🔄 Switching to fresh node from hint: {node}{CLI_CLR}")
                        try:
                            self._client = GonkaOpenAI(gonka_private_key=self.gonka_private_key, source_url=node)
                            self._current_node = node
                            self._tried_nodes.add(node)
                            return True
                        except Exception:
                            continue

        available_nodes = get_available_nodes()
        new_node = None
        
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


class OpenRouterChatModel(BaseChatModel):
    """
    LangChain ChatModel wrapper for OpenRouter API.
    Uses standard OpenAI-compatible API with OpenRouter base URL.
    
    Environment variables:
        OPENAI_API_KEY: OpenRouter API key (sk-or-...)
        OPENAI_BASE_URL: OpenRouter base URL (https://openrouter.ai/api/v1)
        HTTP_REFERER: For OpenRouter leaderboard (optional)
        X_TITLE: App title for OpenRouter (optional)
    """
    model_name: str = Field(alias="model")
    api_key: str = Field(default_factory=lambda: os.getenv("OPENAI_API_KEY"))
    base_url: str = Field(default_factory=lambda: os.getenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1"))
    max_retries: int = 3
    request_timeout: int = 120
    
    _client: Any = PrivateAttr(default=None)
    _http_referer: str = PrivateAttr(default=None)
    _x_title: str = PrivateAttr(default=None)
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._http_referer = os.getenv("HTTP_REFERER", "https://erc3.timetoact.at")
        self._x_title = os.getenv("X_TITLE", "ERC3-dev")
        self._init_client()
    
    def _init_client(self):
        """Initialize OpenAI client configured for OpenRouter."""
        from openai import OpenAI
        
        self._client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.request_timeout
        )
        print(f"🌐 OpenRouter client initialized for model: {self.model_name}")
        print(f"   Base URL: {self.base_url}")
    
    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Any = None,
        **kwargs
    ) -> ChatResult:
        """Generate response using OpenRouter API."""
        openai_messages = self._convert_messages(messages)
        
        last_error = None
        for attempt in range(self.max_retries):
            try:
                response = self._client.chat.completions.create(
                    model=self.model_name,
                    messages=openai_messages,
                    stop=stop,
                    temperature=kwargs.get("temperature", 0.0),
                    max_tokens=kwargs.get("max_tokens", 4096),
                    extra_headers={
                        "HTTP-Referer": self._http_referer,
                        "X-Title": self._x_title
                    }
                )
                
                content = response.choices[0].message.content or ""
                generation = ChatGeneration(
                    message=AIMessage(content=content),
                    generation_info={
                        "model": response.model,
                        "finish_reason": response.choices[0].finish_reason
                    }
                )
                
                usage = {}
                if response.usage:
                    usage = {
                        "prompt_tokens": response.usage.prompt_tokens,
                        "completion_tokens": response.usage.completion_tokens,
                        "total_tokens": response.usage.total_tokens
                    }
                
                return ChatResult(
                    generations=[generation],
                    llm_output={"token_usage": usage, "model_name": response.model}
                )
                
            except Exception as e:
                last_error = e
                error_str = str(e).lower()
                
                if "rate" in error_str or "429" in error_str:
                    wait_time = (attempt + 1) * 5
                    print(f"{CLI_YELLOW}⚠ Rate limited. Waiting {wait_time}s...{CLI_CLR}")
                    time.sleep(wait_time)
                    continue
                
                print(f"{CLI_YELLOW}⚠ OpenRouter error (attempt {attempt+1}/{self.max_retries}): {e}{CLI_CLR}")
                if attempt < self.max_retries - 1:
                    time.sleep(2)
        
        raise last_error or Exception("OpenRouter API call failed")
    
    def _convert_messages(self, messages: List[BaseMessage]) -> List[Dict]:
        """Convert LangChain messages to OpenAI format."""
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
        return "openrouter-chat-model"


def get_llm(model_name: str, backend: str = "gonka") -> BaseChatModel:
    """
    Factory function to get the appropriate LLM based on backend.
    
    Args:
        model_name: Model identifier
        backend: "gonka" or "openrouter"
    
    Returns:
        LangChain ChatModel instance
    """
    if backend == "openrouter":
        return OpenRouterChatModel(model=model_name)
    else:
        return GonkaChatModel(model=model_name)

