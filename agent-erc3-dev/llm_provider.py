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
- Periodic NTP time sync to prevent "signature is too old" errors
"""

import os
import time
import random
import threading
import socket
import struct
import contextvars
from email.utils import parsedate_to_datetime
from datetime import timezone
from typing import Any, List, Optional, Dict
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, AIMessage, SystemMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatResult, ChatGeneration
from pydantic import Field, PrivateAttr

from gonka_openai import GonkaOpenAI
import gonka_openai.utils as gonka_utils
from utils import get_available_nodes, fetch_active_nodes, GENESIS_NODES, CLI_RED, CLI_YELLOW, CLI_CYAN, CLI_CLR, NoAvailableNodesError


# AICODE-NOTE: ACSC (Adaptive Clock Skew Compensation) for per-node time adjustment
# Each Gonka node has different clock drift. We learn and apply per-node offsets.
_current_node_offset = contextvars.ContextVar('node_offset', default=0.0)


# AICODE-NOTE: NTP time synchronization to prevent "signature is too old" errors
# Gonka requires accurate timestamps; system clock drift causes request rejection
class NTPTimeSync:
    """Periodic NTP time synchronization without sudo."""

    NTP_SERVERS = ["time.apple.com", "pool.ntp.org", "time.google.com"]
    SYNC_INTERVAL = 120  # seconds between syncs

    def __init__(self):
        self._lock = threading.Lock()
        self._last_sync = 0
        self._offset_ns = 0  # offset to add to local time
        self._sync_thread = None
        self._running = False

    def _query_ntp(self, server: str, timeout: float = 2.0) -> Optional[float]:
        """Query NTP server and return offset in seconds."""
        try:
            # NTP packet: 48 bytes, first byte = 0x1B (client mode)
            packet = b'\x1b' + 47 * b'\0'

            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(timeout)

            send_time = time.time()
            sock.sendto(packet, (server, 123))
            data, _ = sock.recvfrom(48)
            recv_time = time.time()
            sock.close()

            # Extract transmit timestamp (bytes 40-47)
            t = struct.unpack('!12I', data)[10]
            t -= 2208988800  # NTP epoch (1900) to Unix epoch (1970)

            # Calculate offset: NTP_time - local_time
            round_trip = recv_time - send_time
            offset = t - (send_time + round_trip / 2)
            return offset

        except Exception:
            return None

    def sync_once(self) -> bool:
        """Perform one NTP sync, return True if successful."""
        for server in self.NTP_SERVERS:
            offset = self._query_ntp(server)
            if offset is not None:
                with self._lock:
                    self._offset_ns = int(offset * 1_000_000_000)
                    self._last_sync = time.time()

                    # AICODE-NOTE: Safe patching with hasattr check to survive SDK updates
                    if hasattr(gonka_utils, "_wall_base") and hasattr(gonka_utils, "_perf_base"):
                        gonka_utils._wall_base = time.time_ns() + self._offset_ns
                        gonka_utils._perf_base = time.perf_counter_ns()
                    else:
                        print(f"{CLI_YELLOW}⚠ WARNING: gonka_utils structure changed, NTP patch skipped{CLI_CLR}")

                if abs(offset) > 0.5:  # Only log if significant drift
                    print(f"{CLI_YELLOW}⏱ NTP sync: clock offset {offset:+.2f}s (corrected){CLI_CLR}")
                return True
        return False

    def _sync_loop(self):
        """Background sync loop."""
        while self._running:
            try:
                self.sync_once()
            except Exception:
                pass
            # Sleep in small intervals to allow clean shutdown
            for _ in range(self.SYNC_INTERVAL):
                if not self._running:
                    break
                time.sleep(1)

    def start(self):
        """Start periodic sync in background thread."""
        if self._sync_thread and self._sync_thread.is_alive():
            return

        # Initial sync
        if self.sync_once():
            print(f"{CLI_CYAN}⏱ NTP time sync enabled (every {self.SYNC_INTERVAL}s){CLI_CLR}")

        self._running = True
        self._sync_thread = threading.Thread(target=self._sync_loop, daemon=True)
        self._sync_thread.start()

    def stop(self):
        """Stop background sync."""
        self._running = False


_ntp_sync = NTPTimeSync()


# AICODE-NOTE: ACSC - Adaptive Clock Skew Compensation
# Different Gonka nodes have different clock drifts. We learn and store per-node offsets.
class NodeOffsetManager:
    """
    Manages per-node clock offsets for Gonka network.

    Problem: Gonka nodes have inconsistent clocks (some ahead, some behind).
    Solution: Learn each node's clock offset from HTTP Date headers and apply
    per-node corrections to request signatures.
    """

    def __init__(self):
        self._offsets: Dict[str, float] = {}  # node_url -> offset_seconds
        self._lock = threading.RLock()

    def update_from_headers(self, node_url: str, headers: dict) -> bool:
        """Parse Date header and update offset for this node."""
        server_date = headers.get("Date") or headers.get("date")
        if not server_date:
            return False

        try:
            # Parse HTTP Date (RFC 2822)
            dt_server = parsedate_to_datetime(server_date)
            ts_server = dt_server.replace(tzinfo=timezone.utc).timestamp()
            ts_local = time.time()

            # Calculate offset: how much server is ahead of us
            offset = ts_server - ts_local

            with self._lock:
                # Exponential moving average to smooth jitter
                old_offset = self._offsets.get(node_url, 0)
                if old_offset == 0:
                    self._offsets[node_url] = offset
                else:
                    self._offsets[node_url] = (old_offset * 0.7) + (offset * 0.3)

            if abs(offset) > 1.0:  # Only log significant skew
                print(f"{CLI_CYAN}⏰ Clock skew learned for {node_url.split('//')[-1]}: {offset:+.2f}s{CLI_CLR}")
            return True

        except Exception as e:
            print(f"{CLI_YELLOW}⚠ Failed to parse clock skew: {e}{CLI_CLR}")
            return False

    def update_from_error(self, node_url: str, error_msg: str) -> float:
        """
        Estimate offset from error message when Date header is unavailable.
        Returns the estimated offset that was applied.
        """
        error_lower = error_msg.lower()
        with self._lock:
            current = self._offsets.get(node_url, 0)

            if "too old" in error_lower or "expired" in error_lower:
                # Our time is behind server - add positive offset
                new_offset = current + 30.0  # Jump 30s forward
                self._offsets[node_url] = new_offset
                print(f"{CLI_CYAN}⏰ Clock skew (too old) for {node_url.split('//')[-1]}: {new_offset:+.1f}s{CLI_CLR}")
                return new_offset

            elif "future" in error_lower:
                # Our time is ahead of server - add negative offset
                new_offset = current - 30.0  # Jump 30s backward
                self._offsets[node_url] = new_offset
                print(f"{CLI_CYAN}⏰ Clock skew (future) for {node_url.split('//')[-1]}: {new_offset:+.1f}s{CLI_CLR}")
                return new_offset

            return current

    def get_offset(self, node_url: str) -> float:
        """Get known offset for a node (0 if unknown)."""
        with self._lock:
            return self._offsets.get(node_url, 0.0)

    def clear(self, node_url: str = None):
        """Clear offset(s) - for testing or reset."""
        with self._lock:
            if node_url:
                self._offsets.pop(node_url, None)
            else:
                self._offsets.clear()


_offset_manager = NodeOffsetManager()


# AICODE-NOTE: Patch SDK's time function for per-node clock compensation
# We monkey-patch hybrid_timestamp_ns to use our contextvar offset
_original_hybrid_timestamp_ns = None
_sdk_patched = False


def _patch_gonka_sdk():
    """Patch Gonka SDK's time function to support per-node offsets."""
    global _original_hybrid_timestamp_ns, _sdk_patched

    if _sdk_patched:
        return

    if not hasattr(gonka_utils, 'hybrid_timestamp_ns'):
        print(f"{CLI_YELLOW}⚠ gonka_utils.hybrid_timestamp_ns not found, ACSC disabled{CLI_CLR}")
        return

    _original_hybrid_timestamp_ns = gonka_utils.hybrid_timestamp_ns

    def _patched_hybrid_timestamp_ns() -> int:
        """
        Patched time function that applies per-node offset from contextvar.
        This allows different threads to use different time offsets.
        """
        # Get offset for current context (node)
        offset_sec = _current_node_offset.get()
        offset_ns = int(offset_sec * 1_000_000_000)

        # Standard Gonka logic + our offset
        if hasattr(gonka_utils, '_wall_base') and hasattr(gonka_utils, '_perf_base'):
            base_time = gonka_utils._wall_base + (time.perf_counter_ns() - gonka_utils._perf_base)
            return base_time + offset_ns
        else:
            # Fallback if SDK structure changed
            return time.time_ns() + offset_ns

    gonka_utils.hybrid_timestamp_ns = _patched_hybrid_timestamp_ns
    _sdk_patched = True
    print(f"{CLI_CYAN}💉 Gonka SDK patched for ACSC (per-node clock compensation){CLI_CLR}")


# AICODE-NOTE: Global rate limiter for Gonka nodes
# Optimization B: Adaptive min_interval based on available nodes count
class NodeRateLimiter:
    """Thread-safe rate limiter for Gonka nodes with adaptive intervals."""

    def __init__(self, min_interval: float = 0.2, max_concurrent_per_node: int = 3):
        self._lock = threading.Lock()
        self._last_request_time: Dict[str, float] = {}
        self._active_requests: Dict[str, int] = {}
        self._base_min_interval = min_interval
        self._max_concurrent = max_concurrent_per_node
        self._available_nodes_count = 0

    def set_available_nodes_count(self, count: int):
        """Update node count for adaptive interval calculation."""
        with self._lock:
            self._available_nodes_count = count

    def _get_adaptive_interval(self) -> float:
        """Return adaptive min_interval based on available nodes."""
        # More nodes = lower interval needed (less contention per node)
        if self._available_nodes_count > 20:
            return 0.1
        elif self._available_nodes_count > 10:
            return 0.15
        return self._base_min_interval

    def acquire(self, node: str) -> bool:
        with self._lock:
            now = time.time()
            active = self._active_requests.get(node, 0)
            if active >= self._max_concurrent:
                return False
            last_time = self._last_request_time.get(node, 0)
            min_interval = self._get_adaptive_interval()
            if now - last_time < min_interval:
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


# AICODE-NOTE: TrafficController - Central rate limiter for GonkaGate (100 RPM per IP)
# Two-level rate limiting in Gonka Network:
# Level 1: GonkaGate - 100 RPM per IP (global limit)
# Level 2: Node Overload - vLLM queue overflow (per-node, handled by NodeRateLimiter)
class TrafficController:
    """
    Central traffic controller. Manages global RPM (100).
    Uses Token Bucket algorithm with smooth refill to prevent bursting.
    """

    def __init__(self, max_rpm: int = 90):  # 90 to leave safety margin
        self.rpm_limit = max_rpm
        self.tokens_bucket = float(max_rpm)  # Start full
        self.last_refill = time.time()
        self._lock = threading.RLock()
        self._wait_count = 0  # Stats: how many times we had to wait

    def wait_for_token(self, timeout: float = 120.0) -> bool:
        """
        Token Bucket algorithm for respecting 100 RPM.

        Refills tokens continuously (rpm_limit tokens per 60 seconds).
        If no tokens available, waits until one is available.

        Returns:
            True if token acquired, False if timeout exceeded.
        """
        start_wait = time.time()

        while True:
            with self._lock:
                now = time.time()
                # Refill tokens: add (elapsed_time / 60) * rpm_limit tokens
                elapsed = now - self.last_refill
                refill_amount = (elapsed / 60.0) * self.rpm_limit
                self.tokens_bucket = min(self.rpm_limit, self.tokens_bucket + refill_amount)
                self.last_refill = now

                if self.tokens_bucket >= 1.0:
                    self.tokens_bucket -= 1.0
                    return True

                # Calculate wait time for 1 token
                tokens_needed = 1.0 - self.tokens_bucket
                wait_time = (tokens_needed / self.rpm_limit) * 60.0

            # Check timeout
            if time.time() - start_wait + wait_time > timeout:
                print(f"{CLI_YELLOW}⚠ TrafficController: timeout waiting for token{CLI_CLR}")
                return False

            # First time waiting? Log it
            with self._lock:
                self._wait_count += 1
                if self._wait_count == 1 or self._wait_count % 10 == 0:
                    print(f"{CLI_CYAN}⏳ TrafficController: rate limit reached, waiting {wait_time:.1f}s...{CLI_CLR}")

            # Wait with small random jitter to prevent thundering herd
            time.sleep(wait_time + random.random() * 0.1)

    def record_429_error(self):
        """
        Called when we receive 429 error from GonkaGate.
        Reduce tokens to slow down further.
        """
        with self._lock:
            # Halve remaining tokens to back off
            self.tokens_bucket = max(0, self.tokens_bucket / 2)
            print(f"{CLI_YELLOW}⚡ TrafficController: 429 received, backing off (bucket: {self.tokens_bucket:.1f}){CLI_CLR}")

    def get_stats(self) -> dict:
        """Return current stats for debugging."""
        with self._lock:
            return {
                "tokens": self.tokens_bucket,
                "rpm_limit": self.rpm_limit,
                "wait_count": self._wait_count
            }


_traffic_controller = TrafficController(max_rpm=90)


# AICODE-NOTE: TopologyManager - Background worker for node discovery
# Isolates inference threads from slow Genesis API calls
class TopologyManager:
    """
    Background manager that maintains an up-to-date list of inference nodes.
    Isolates the main inference thread from slow Genesis API calls.

    Key features:
    - Background refresh every 30-60s
    - Caches node list to avoid blocking on discovery
    - Filters nodes by model capability
    - Does NOT return genesis nodes (they can't do inference)
    """

    def __init__(self, model_filter: str = "Qwen/Qwen3-235B-A22B-Instruct-2507-FP8",
                 refresh_interval: int = 45):
        self.model_filter = model_filter
        self.refresh_interval = refresh_interval
        self._nodes: List[str] = []
        self._lock = threading.RLock()
        self._running = False
        self._last_update = 0
        self._update_thread = None

    def start(self):
        """Start background topology updates."""
        if self._running:
            return

        self._running = True
        # First refresh is synchronous to have nodes immediately
        self._refresh_topology()

        # Start background refresh
        self._update_thread = threading.Thread(target=self._loop, daemon=True)
        self._update_thread.start()
        print(f"{CLI_CYAN}📡 TopologyManager started (refresh every {self.refresh_interval}s){CLI_CLR}")

    def stop(self):
        """Stop background updates."""
        self._running = False

    def get_nodes(self) -> List[str]:
        """Get current list of available inference nodes."""
        with self._lock:
            if not self._nodes:
                # If empty, try sync refresh as failover
                print(f"{CLI_YELLOW}⚠ Node list empty, forcing sync refresh...{CLI_CLR}")
                self._refresh_topology()
            return list(self._nodes)

    def get_node_count(self) -> int:
        """Get count of available nodes without copying list."""
        with self._lock:
            return len(self._nodes)

    def _loop(self):
        """Background refresh loop."""
        while self._running:
            # Sleep in small intervals to allow clean shutdown
            for _ in range(self.refresh_interval):
                if not self._running:
                    return
                time.sleep(1)
            try:
                self._refresh_topology()
            except Exception as e:
                print(f"{CLI_YELLOW}⚠ Topology background update failed: {e}{CLI_CLR}")

    def _refresh_topology(self):
        """Fetch fresh node list from Genesis nodes."""
        sources = list(GENESIS_NODES)
        random.shuffle(sources)

        found_nodes = []

        # Try up to 3 genesis nodes
        for source in sources[:3]:
            try:
                url = f"{source}/v1/epochs/current/participants"
                import requests
                resp = requests.get(url, timeout=5)  # Short timeout for discovery
                if resp.status_code != 200:
                    continue

                data = resp.json()
                participants = data.get("active_participants", {}).get("participants", [])

                # Filter by model (case-insensitive for robustness)
                model_lower = self.model_filter.lower()
                filtered = [
                    p.get("inference_url") for p in participants
                    if any(model_lower in m.lower() for m in p.get("models", []))
                    and p.get("inference_url")
                ]

                if filtered:
                    found_nodes = filtered
                    break
            except Exception:
                continue

        with self._lock:
            if found_nodes:
                old_count = len(self._nodes)
                self._nodes = found_nodes
                self._last_update = time.time()
                if old_count != len(found_nodes):
                    print(f"{CLI_CYAN}📡 Topology updated: {len(found_nodes)} nodes for {self.model_filter}{CLI_CLR}")
            elif not self._nodes:
                # CRITICAL: Never add Genesis nodes here - they can't do inference!
                print(f"{CLI_YELLOW}✗ Failed to find any active inference nodes{CLI_CLR}")


_topology_manager = TopologyManager()


# AICODE-NOTE: Optimization A - Round-robin node assignment for thread distribution
# AICODE-NOTE: Optimization C - Preconnect warming at startup
class NodePool:
    """
    Thread-safe pool of Gonka nodes with performance tracking.
    Tracks successful nodes and their response times.
    Supports round-robin assignment and preconnect warming.
    """

    def __init__(self, blacklist_duration: float = 60.0):
        self._lock = threading.Lock()
        self._good_nodes: Dict[str, Dict] = {}
        self._blacklist: Dict[str, float] = {}
        self._blacklist_duration = blacklist_duration
        self._warmed_nodes: List[str] = []  # Optimization C: preconnected nodes
        self._assignment_counter = 0  # Optimization A: for round-robin

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

    def get_node_round_robin(self) -> Optional[str]:
        """Optimization A: Get next node in round-robin fashion to distribute load."""
        with self._lock:
            now = time.time()
            # First try warmed nodes
            if self._warmed_nodes:
                # Filter out blacklisted nodes inline (avoid nested lock)
                available = [n for n in self._warmed_nodes if now >= self._blacklist.get(n, 0)]
                if available:
                    idx = self._assignment_counter % len(available)
                    self._assignment_counter += 1
                    return available[idx]
            # Fallback to best nodes (get_best_nodes would cause deadlock, inline logic)
            candidates = []
            for node, stats in self._good_nodes.items():
                if node in self._blacklist and self._blacklist[node] > now:
                    continue
                if now - stats["last_success"] > 600:
                    continue
                score = stats["success_count"] / max(0.1, stats["avg_response_time"])
                candidates.append((node, score))
            candidates.sort(key=lambda x: x[1], reverse=True)
            best = [node for node, _ in candidates[:10]]
            if best:
                idx = self._assignment_counter % len(best)
                self._assignment_counter += 1
                return best[idx]
            return None

    def get_node_p2c(self, rate_limiter: 'NodeRateLimiter') -> Optional[str]:
        """
        Power of Two Choices (P2C) load balancing.

        Pick 2 random nodes from available pool, choose the one with:
        1. Lower current active requests
        2. If tied, better historical latency

        P2C is mathematically proven to work better than round-robin
        for heterogeneous systems (different GPUs, network speeds).
        """
        # Get candidates from TopologyManager (not genesis nodes!)
        candidates = _topology_manager.get_nodes()
        if not candidates:
            return None

        with self._lock:
            now = time.time()
            # Filter out blacklisted nodes
            valid = [n for n in candidates if now >= self._blacklist.get(n, 0)]

            if not valid:
                return None

            if len(valid) == 1:
                return valid[0]

            # Pick 2 random nodes
            n1, n2 = random.sample(valid, 2)

            # Get current load from rate limiter
            load1 = rate_limiter._active_requests.get(n1, 0)
            load2 = rate_limiter._active_requests.get(n2, 0)

            # Choose less loaded node
            if load1 < load2:
                return n1
            elif load2 < load1:
                return n2
            else:
                # If tied, choose node with better historical latency
                stats1 = self._good_nodes.get(n1, {"avg_response_time": 10})
                stats2 = self._good_nodes.get(n2, {"avg_response_time": 10})
                return n1 if stats1["avg_response_time"] <= stats2["avg_response_time"] else n2

    def warmup_nodes(self, nodes: List[str], max_nodes: int = 5) -> List[str]:
        """Optimization C: Ping nodes to measure latency, store best ones."""
        import concurrent.futures

        def ping_node(node: str) -> tuple:
            try:
                start = time.time()
                import requests
                resp = requests.get(f"{node}/v1/models", timeout=5)
                latency = time.time() - start
                if resp.status_code == 200:
                    return (node, latency)
            except Exception:
                pass
            return (node, float('inf'))

        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(10, len(nodes))) as executor:
            futures = {executor.submit(ping_node, n): n for n in nodes[:20]}
            for future in concurrent.futures.as_completed(futures, timeout=10):
                try:
                    result = future.result()
                    if result[1] < float('inf'):
                        results.append(result)
                except Exception:
                    pass

        # Sort by latency, take best
        results.sort(key=lambda x: x[1])
        warmed = [n for n, _ in results[:max_nodes]]

        with self._lock:
            self._warmed_nodes = warmed
            # Pre-populate good_nodes with initial latency
            for node, latency in results[:max_nodes]:
                if node not in self._good_nodes:
                    self._good_nodes[node] = {
                        "avg_response_time": latency,
                        "success_count": 1,
                        "last_success": time.time()
                    }

        return warmed

    def is_blacklisted(self, node: str) -> bool:
        with self._lock:
            return time.time() < self._blacklist.get(node, 0)


_node_pool = NodePool(blacklist_duration=60.0)
_warmup_done = False
_warmup_lock = threading.Lock()


def warmup_gonka_nodes():
    """Optimization C: Warmup nodes at startup for faster first requests."""
    global _warmup_done
    with _warmup_lock:
        if _warmup_done:
            return
        _warmup_done = True

    # Patch SDK for ACSC (Adaptive Clock Skew Compensation)
    _patch_gonka_sdk()

    # Start periodic NTP sync to prevent "signature is too old" errors
    _ntp_sync.start()

    # Start TopologyManager for background node discovery
    _topology_manager.start()

    print(f"{CLI_CYAN}🔥 Warming up Gonka nodes...{CLI_CLR}")
    # Get nodes from TopologyManager (or fallback to direct fetch)
    nodes = _topology_manager.get_nodes()
    if not nodes:
        # Fallback to direct fetch if TopologyManager hasn't populated yet
        nodes = get_available_nodes()

    _node_rate_limiter.set_available_nodes_count(len(nodes))

    warmed = _node_pool.warmup_nodes(nodes, max_nodes=8)
    if warmed:
        print(f"{CLI_CYAN}✓ Warmed {len(warmed)} nodes: {', '.join(n.split('/')[-1] for n in warmed[:3])}...{CLI_CLR}")
    else:
        print(f"{CLI_YELLOW}⚠ No nodes responded to warmup{CLI_CLR}")


class GonkaChatModel(BaseChatModel):
    """LangChain ChatModel wrapper for Gonka Network with automatic node failover."""

    model_name: str = Field(alias="model")
    gonka_private_key: str = Field(default_factory=lambda: os.getenv("GONKA_PRIVATE_KEY"))
    max_retries_per_node: int = 3
    max_node_switches: int = 10
    request_timeout: int = 180  # AICODE-NOTE: Increased for Qwen-235B (can take 2-3 min on loaded nodes)
    # AICODE-NOTE: Global retry params for network-wide outages (all nodes failing)
    max_global_retries: int = 5  # Retry entire node cycle this many times (increased for unstable network)
    global_retry_base_delay: float = 45.0  # Base delay in seconds (exponential backoff)

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

    def _reset_for_global_retry(self):
        """Reset state for global retry after all nodes failed."""
        self._tried_nodes.clear()
        self._client = None
        self._current_node = None
        # Clear blacklist to give nodes a fresh chance
        _node_pool._blacklist.clear()

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[Any] = None,
        **kwargs: Any,
    ) -> ChatResult:
        openai_messages = self._convert_messages(messages)

        # AICODE-NOTE: Global retry loop for network-wide outages
        for global_retry in range(self.max_global_retries + 1):
            if global_retry > 0:
                # Exponential backoff: 30s, 60s, 120s
                delay = self.global_retry_base_delay * (2 ** (global_retry - 1))
                print(f"{CLI_YELLOW}⏳ Global retry {global_retry}/{self.max_global_retries}: "
                      f"waiting {delay:.0f}s before re-trying all nodes...{CLI_CLR}")
                time.sleep(delay)
                self._reset_for_global_retry()
                # Force topology refresh and re-warmup nodes
                _topology_manager._refresh_topology()
                nodes = _topology_manager.get_nodes()
                if nodes:
                    _node_pool.warmup_nodes(nodes, max_nodes=5)

            if self._client is None:
                self._connect_initial()

            all_nodes_exhausted = True
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

                    if self._switch_node(hint_node=hint_node):
                        all_nodes_exhausted = False
                    else:
                        # No more nodes to try in this cycle
                        break

            # All nodes failed in this cycle - will retry if we have global retries left
            if all_nodes_exhausted or node_attempt >= self.max_node_switches - 1:
                if global_retry < self.max_global_retries:
                    print(f"{CLI_YELLOW}⚠ All {self.max_node_switches} nodes exhausted, "
                          f"initiating global retry...{CLI_CLR}")
                    continue  # Go to next global retry
                else:
                    break  # No more retries

        raise Exception("All Gonka nodes failed after global retries.")

    def _call_with_retry(self, messages, stop, **kwargs):
        """
        Call LLM with retry logic and ACSC (Adaptive Clock Skew Compensation).

        ACSC allows us to handle nodes with different clock drifts by learning
        and applying per-node time offsets based on error responses.
        """
        # AICODE-NOTE: TrafficController - respect global 100 RPM limit (GonkaGate)
        if not _traffic_controller.wait_for_token(timeout=120.0):
            raise Exception("TrafficController: timeout waiting for rate limit token")

        last_error = None
        node = self._current_node

        # ACSC: Get known offset for this node and set in context
        offset = _offset_manager.get_offset(node) if node else 0.0
        token = _current_node_offset.set(offset)

        try:
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
                    error_str = str(e).lower()

                    # ACSC: Detect clock skew errors and learn offset
                    is_clock_error = ("signature" in error_str and
                                      ("old" in error_str or "future" in error_str or "expired" in error_str))

                    if is_clock_error and node:
                        print(f"{CLI_CYAN}🕰 Clock skew detected on {node.split('//')[-1]}. Learning offset...{CLI_CLR}")

                        # Try to learn from HTTP Date header if available
                        headers_learned = False
                        if hasattr(e, 'response') and e.response:
                            headers = getattr(e.response, 'headers', {})
                            if headers:
                                headers_learned = _offset_manager.update_from_headers(node, headers)

                        # Fallback: estimate from error message
                        if not headers_learned:
                            _offset_manager.update_from_error(node, str(e))

                        # Update context with new offset for retry
                        new_offset = _offset_manager.get_offset(node)
                        _current_node_offset.set(new_offset)
                        print(f"{CLI_CYAN}🔄 Retrying with adjusted clock (offset: {new_offset:+.1f}s)...{CLI_CLR}")
                        continue  # Retry with new offset

                    # AICODE-NOTE: Smart 429/502/503/504 handling for TrafficController
                    # 429 = GonkaGate rate limit (global) - back off via TrafficController
                    # 502/503/504 = Node overload (vLLM queue full) - blacklist node immediately
                    if "429" in error_str or "rate limit" in error_str:
                        _traffic_controller.record_429_error()
                        # Still raise to switch nodes, but TrafficController will throttle

                    # Node failures: aggressive blacklisting
                    is_node_failure = any(code in error_str for code in [
                        "502", "503", "504", "timeout", "connection error",
                        "connection aborted", "connection refused", "eof"
                    ])
                    if is_node_failure and node:
                        print(f"{CLI_YELLOW}📉 Node {node.split('//')[-1]} failed. Blacklisting...{CLI_CLR}")
                        _node_pool.record_failure(node)

                    # Other critical errors - fail fast to switch nodes
                    critical_errors = [
                        "connection aborted", "remote end closed", "connection refused",
                        "connecttimeouterror", "remotedisconnected", "transfer agent capacity reached",
                        "429", "unable to validate request", "invalid signature",
                        "request timed out", "read timed out", "rate limit", "timeout",
                        "502", "503", "504", "service unavailable", "eof"
                    ]

                    # Record failure for any critical error (if not already done above)
                    if node and not is_node_failure:
                        _node_pool.record_failure(node)

                    if any(ce in error_str for ce in critical_errors):
                        print(f"{CLI_YELLOW}⚠ Critical error on {self._current_node}: {e}{CLI_CLR}")
                        raise e

                    last_error = e
                    print(f"{CLI_YELLOW}⚠ Retry {attempt+1}/{self.max_retries_per_node} on {self._current_node}: {e}{CLI_CLR}")
                    wait_time = (attempt + 1) * 1.5 + random.random() * 0.5
                    if attempt < self.max_retries_per_node - 1:
                        time.sleep(wait_time)

            raise last_error
        finally:
            # ACSC: Reset context to avoid polluting other threads
            _current_node_offset.reset(token)

    def _connect_initial(self):
        # Optimization C: Run warmup on first connection
        warmup_gonka_nodes()

        fixed_node = os.getenv("GONKA_NODE_URL")
        if fixed_node:
            print(f"{CLI_CYAN}🔗 Using fixed node: {fixed_node}{CLI_CLR}")
            self._client = self._create_gonka_client(fixed_node)
            self._current_node = fixed_node
            return

        # P2C: Power of Two Choices for better load balancing
        pool_node = _node_pool.get_node_p2c(_node_rate_limiter)
        if pool_node and pool_node not in self._tried_nodes:
            try:
                print(f"{CLI_CYAN}🔗 Using P2C selected node: {pool_node}{CLI_CLR}")
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

        # Discovery: try fresh nodes from TopologyManager
        nodes = _topology_manager.get_nodes()
        if not nodes:
            raise NoAvailableNodesError("No inference nodes available from TopologyManager")

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

        # CRITICAL: Do NOT fallback to genesis nodes - they can't do inference!
        raise NoAvailableNodesError("All inference nodes failed, no fallback available")

    def _switch_node(self, hint_node: str = None) -> bool:
        # Try P2C selection first for optimal load balancing
        p2c_node = _node_pool.get_node_p2c(_node_rate_limiter)
        if p2c_node and p2c_node not in self._tried_nodes and p2c_node != self._current_node:
            print(f"{CLI_CYAN}🔄 Switching to P2C selected node: {p2c_node}{CLI_CLR}")
            try:
                self._client = self._create_gonka_client(p2c_node)
                self._current_node = p2c_node
                self._tried_nodes.add(p2c_node)
                return True
            except Exception:
                _node_pool.record_failure(p2c_node)

        # Try proven good nodes
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

        # Get nodes from TopologyManager (not genesis!)
        available_nodes = _topology_manager.get_nodes()
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

        # Last resort: try any non-blacklisted node
        non_blacklisted = [n for n in available_nodes if not _node_pool.is_blacklisted(n)]
        if non_blacklisted:
            node = random.choice(non_blacklisted)
            try:
                self._client = self._create_gonka_client(node)
                self._current_node = node
                return True
            except Exception:
                pass

        # CRITICAL: Do NOT fallback to genesis nodes - they can't do inference!
        print(f"{CLI_RED}✗ No inference nodes available for failover{CLI_CLR}")
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
