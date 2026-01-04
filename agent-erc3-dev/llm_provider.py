"""
LLM Provider Module - Unified interface for multiple LLM backends.

Supports:
- Gonka Network (decentralized inference with automatic node failover)
- OpenRouter (commercial API with multiple model providers)

AICODE-NOTE: Gonka Network optimization for parallel execution:
- max_retries=0 in GonkaOpenAI to avoid internal OpenAI retries (faster failover)
- Per-node rate limiting to prevent 429 errors
- NodePool with performance tracking for smart node selection
- Jitter between requests to reduce contention
"""

import os
import time
import random
import threading
from typing import Any, List, Optional, Dict
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, AIMessage, SystemMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatResult, ChatGeneration
from pydantic import Field, PrivateAttr

from gonka_openai import GonkaOpenAI
from utils import get_available_nodes, GENESIS_NODES, CLI_RED, CLI_YELLOW, CLI_CYAN, CLI_CLR


# AICODE-NOTE: Global rate limiter for Gonka nodes
class NodeRateLimiter:
    """Thread-safe rate limiter for Gonka nodes."""

    def __init__(self, min_interval: float = 0.2, max_concurrent_per_node: int = 3):
        self._lock = threading.Lock()
        self._last_request_time: Dict[str, float] = {}
        self._active_requests: Dict[str, int] = {}
        self._min_interval = min_interval
        self._max_concurrent = max_concurrent_per_node

    def acquire(self, node: str) -> bool:
        with self._lock:
            now = time.time()
            active = self._active_requests.get(node, 0)
            if active >= self._max_concurrent:
                return False
            last_time = self._last_request_time.get(node, 0)
            if now - last_time < self._min_interval:
                return False
            self._active_requests[node] = active + 1
            self._last_request_time[node] = now
            return True

    def release(self, node: str):
        with self._lock:
            active = self._active_requests.get(node, 0)
            if active > 0:
                self._active_requests[node] = active - 1

    def wait_for_slot(self, node: str, timeout: float = 5.0) -> bool:
        start = time.time()
        while time.time() - start < timeout:
            if self.acquire(node):
                return True
            time.sleep(0.05 + random.random() * 0.1)
        return False


_node_rate_limiter = NodeRateLimiter()


class NodePool:
    """
    Thread-safe pool of Gonka nodes with performance tracking.
    Tracks successful nodes and their response times.
    """

    def __init__(self, blacklist_duration: float = 60.0):
        self._lock = threading.Lock()
        self._good_nodes: Dict[str, Dict] = {}
        self._blacklist: Dict[str, float] = {}
        self._blacklist_duration = blacklist_duration

    def record_success(self, node: str, response_time: float):
        with self._lock:
            now = time.time()
            if node in self._good_nodes:
                stats = self._good_nodes[node]
                stats["avg_response_time"] = stats["avg_response_time"] * 0.7 + response_time * 0.3
                stats["success_count"] += 1
                stats["last_success"] = now
            else:
                self._good_nodes[node] = {
                    "avg_response_time": response_time,
                    "success_count": 1,
                    "last_success": now
                }
            self._blacklist.pop(node, None)

    def record_failure(self, node: str):
        with self._lock:
            self._blacklist[node] = time.time() + self._blacklist_duration
            if node in self._good_nodes:
                self._good_nodes[node]["success_count"] = max(0, self._good_nodes[node]["success_count"] - 5)

    def get_best_nodes(self, count: int = 5) -> List[str]:
        with self._lock:
            now = time.time()
            self._blacklist = {n: t for n, t in self._blacklist.items() if t > now}
            candidates = []
            for node, stats in self._good_nodes.items():
                if node in self._blacklist:
                    continue
                if now - stats["last_success"] > 600:
                    continue
                score = stats["success_count"] / max(0.1, stats["avg_response_time"])
                candidates.append((node, score))
            candidates.sort(key=lambda x: x[1], reverse=True)
            return [node for node, _ in candidates[:count]]

    def get_random_good_node(self) -> Optional[str]:
        best = self.get_best_nodes(count=5)
        return random.choice(best) if best else None

    def is_blacklisted(self, node: str) -> bool:
        with self._lock:
            return time.time() < self._blacklist.get(node, 0)


_node_pool = NodePool(blacklist_duration=60.0)


class GonkaChatModel(BaseChatModel):
    """LangChain ChatModel wrapper for Gonka Network with automatic node failover."""

    model_name: str = Field(alias="model")
    gonka_private_key: str = Field(default_factory=lambda: os.getenv("GONKA_PRIVATE_KEY"))
    max_retries_per_node: int = 3
    max_node_switches: int = 10
    request_timeout: int = 60

    _client: Optional[GonkaOpenAI] = PrivateAttr(default=None)
    _current_node: Optional[str] = PrivateAttr(default=None)
    _tried_nodes: set = PrivateAttr(default_factory=set)
    _last_successful_node: Optional[str] = None

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not isinstance(self._tried_nodes, set):
            self._tried_nodes = set()

    def _extract_hint_url(self, error_msg: str) -> Optional[str]:
        if "Try another TA from" in error_msg:
            try:
                import re
                match = re.search(r'(http://[^\s]+/participants)', error_msg)
                if match:
                    return match.group(1).split("/v1/")[0]
            except Exception:
                pass
        return None

    def _create_gonka_client(self, node: str) -> GonkaOpenAI:
        """Create GonkaOpenAI with max_retries=0 for faster failover."""
        return GonkaOpenAI(
            gonka_private_key=self.gonka_private_key,
            source_url=node,
            max_retries=0,
            timeout=self.request_timeout
        )

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

                if self._current_node and isinstance(self._current_node, str):
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
                    generations=[ChatGeneration(message=AIMessage(content=message_content))],
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
        node = self._current_node

        for attempt in range(self.max_retries_per_node):
            try:
                if node and not _node_rate_limiter.wait_for_slot(node, timeout=10.0):
                    print(f"{CLI_YELLOW}⚠ Rate limit timeout for {node}{CLI_CLR}")
                    raise Exception("Rate limit timeout")

                start_time = time.time()
                try:
                    result = self._client.chat.completions.create(
                        model=self.model_name,
                        messages=messages,
                        stop=stop,
                        temperature=kwargs.get("temperature", 0.0),
                        timeout=self.request_timeout
                    )
                    response_time = time.time() - start_time
                    if node:
                        _node_pool.record_success(node, response_time)
                    return result
                finally:
                    if node:
                        _node_rate_limiter.release(node)

            except Exception as e:
                if node:
                    _node_pool.record_failure(node)

                error_str = str(e).lower()
                critical_errors = [
                    "connection aborted", "remote end closed", "connection refused",
                    "connecttimeouterror", "remotedisconnected", "transfer agent capacity reached",
                    "429", "signature is too old", "signature is in the future",
                    "unable to validate request", "invalid signature",
                    "request timed out", "read timed out", "rate limit"
                ]

                if any(ce in error_str for ce in critical_errors):
                    print(f"{CLI_YELLOW}⚠ Critical error on {self._current_node}: {e}{CLI_CLR}")
                    if "signature is too old" in error_str or "invalid signature" in error_str:
                        print(f"{CLI_RED}⚠ System clock behind! Run: sudo sntp -sS time.apple.com{CLI_CLR}")
                    if "signature is in the future" in error_str:
                        print(f"{CLI_RED}⚠ System clock ahead! Run: sudo sntp -sS time.apple.com{CLI_CLR}")
                    raise e

                last_error = e
                print(f"{CLI_YELLOW}⚠ Retry {attempt+1}/{self.max_retries_per_node} on {self._current_node}: {e}{CLI_CLR}")
                wait_time = (attempt + 1) * 1.5 + random.random() * 0.5
                if attempt < self.max_retries_per_node - 1:
                    time.sleep(wait_time)
        raise last_error

    def _connect_initial(self):
        fixed_node = os.getenv("GONKA_NODE_URL")
        if fixed_node:
            print(f"{CLI_CYAN}🔗 Using fixed node: {fixed_node}{CLI_CLR}")
            self._client = self._create_gonka_client(fixed_node)
            self._current_node = fixed_node
            return

        # Try proven good nodes from pool first
        pool_node = _node_pool.get_random_good_node()
        if pool_node and pool_node not in self._tried_nodes:
            try:
                print(f"{CLI_CYAN}🔗 Using proven good node: {pool_node}{CLI_CLR}")
                self._client = self._create_gonka_client(pool_node)
                self._current_node = pool_node
                self._tried_nodes.add(pool_node)
                return
            except Exception as e:
                print(f"{CLI_YELLOW}⚠ Pool node failed: {e}{CLI_CLR}")
                _node_pool.record_failure(pool_node)

        # Try cached node
        cached_node = GonkaChatModel._last_successful_node
        if cached_node and isinstance(cached_node, str) and cached_node not in self._tried_nodes:
            if not _node_pool.is_blacklisted(cached_node):
                try:
                    print(f"{CLI_CYAN}🔗 Reusing last successful node: {cached_node}{CLI_CLR}")
                    self._client = self._create_gonka_client(cached_node)
                    self._current_node = cached_node
                    self._tried_nodes.add(cached_node)
                    return
                except Exception as e:
                    print(f"{CLI_YELLOW}⚠ Cached node failed: {e}{CLI_CLR}")
                    GonkaChatModel._last_successful_node = None
                    _node_pool.record_failure(cached_node)

        # Discovery: try fresh nodes
        nodes = get_available_nodes()
        random.shuffle(nodes)
        for node in nodes[:5]:
            if node in self._tried_nodes or _node_pool.is_blacklisted(node):
                continue
            try:
                print(f"{CLI_YELLOW}🔗 Connecting to: {node}{CLI_CLR}")
                self._client = self._create_gonka_client(node)
                self._current_node = node
                self._tried_nodes.add(node)
                return
            except Exception:
                _node_pool.record_failure(node)
                continue

        fallback = random.choice(GENESIS_NODES)
        self._client = self._create_gonka_client(fallback)
        self._current_node = fallback
        self._tried_nodes.add(fallback)

    def _switch_node(self, hint_node: str = None) -> bool:
        # Try proven good nodes first
        best_nodes = _node_pool.get_best_nodes(count=5)
        for node in best_nodes:
            if node not in self._tried_nodes and node != self._current_node:
                print(f"{CLI_CYAN}🔄 Switching to proven good node: {node}{CLI_CLR}")
                try:
                    self._client = self._create_gonka_client(node)
                    self._current_node = node
                    self._tried_nodes.add(node)
                    return True
                except Exception:
                    _node_pool.record_failure(node)
                    continue

        if hint_node:
            print(f"{CLI_CYAN}🔄 Fetching fresh nodes from hint: {hint_node}{CLI_CLR}")
            from utils import fetch_active_nodes
            fresh_nodes = fetch_active_nodes(source_node=hint_node)
            if fresh_nodes:
                random.shuffle(fresh_nodes)
                for node in fresh_nodes:
                    if node not in self._tried_nodes and not _node_pool.is_blacklisted(node):
                        print(f"{CLI_CYAN}🔄 Switching to hint node: {node}{CLI_CLR}")
                        try:
                            self._client = self._create_gonka_client(node)
                            self._current_node = node
                            self._tried_nodes.add(node)
                            return True
                        except Exception:
                            _node_pool.record_failure(node)
                            continue

        available_nodes = get_available_nodes()
        random.shuffle(available_nodes)

        for node in available_nodes:
            if node not in self._tried_nodes and not _node_pool.is_blacklisted(node):
                print(f"{CLI_CYAN}🔄 Switching to node: {node}{CLI_CLR}")
                self._tried_nodes.add(node)
                try:
                    self._client = self._create_gonka_client(node)
                    self._current_node = node
                    return True
                except Exception as e:
                    print(f"{CLI_RED}✗ Failed to connect to {node}: {e}{CLI_CLR}")
                    _node_pool.record_failure(node)

        # Last resort
        non_blacklisted = [n for n in available_nodes if not _node_pool.is_blacklisted(n)]
        if non_blacklisted:
            node = random.choice(non_blacklisted)
            try:
                self._client = self._create_gonka_client(node)
                self._current_node = node
                return True
            except Exception:
                pass

        print(f"{CLI_RED}✗ No nodes available for failover{CLI_CLR}")
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
    """LangChain ChatModel wrapper for OpenRouter API."""

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
    """Factory function to get the appropriate LLM based on backend."""
    if backend == "openrouter":
        return OpenRouterChatModel(model=model_name)
    else:
        return GonkaChatModel(model=model_name)
