import requests
import random
from typing import List

# === Public API ===
__all__ = [
    'CLI',
    'CLI_RED', 'CLI_GREEN', 'CLI_BLUE', 'CLI_YELLOW', 'CLI_CYAN', 'CLI_CLR',
    'GENESIS_NODES',
    'fetch_active_nodes',
    'get_available_nodes',
]

# === Genesis Nodes for Gonka Network ===
GENESIS_NODES = [
    "http://185.216.21.98:8000",
    "http://36.189.234.197:18026",
    "http://36.189.234.237:17241",
    "http://node1.gonka.ai:8000",
    "http://node2.gonka.ai:8000",
    "http://node3.gonka.ai:8000",
    "http://47.236.26.199:8000",
    "http://47.236.19.22:18000",
    "http://gonka.spv.re:8000",
]

class CLI:
    """Terminal color codes and formatting helpers."""
    RED = "\x1B[31m"
    GREEN = "\x1B[32m"
    YELLOW = "\x1B[33m"
    BLUE = "\x1B[34m"
    CYAN = "\x1B[36m"
    RESET = "\x1B[0m"

    @classmethod
    def success(cls, msg: str) -> str:
        return f"{cls.GREEN}✓ {msg}{cls.RESET}"

    @classmethod
    def error(cls, msg: str) -> str:
        return f"{cls.RED}✗ {msg}{cls.RESET}"

    @classmethod
    def warn(cls, msg: str) -> str:
        return f"{cls.YELLOW}⚠ {msg}{cls.RESET}"

    @classmethod
    def info(cls, msg: str) -> str:
        return f"{cls.BLUE}ℹ {msg}{cls.RESET}"


# Backward compatibility aliases (deprecated, use CLI class instead)
CLI_RED = CLI.RED
CLI_GREEN = CLI.GREEN
CLI_BLUE = CLI.BLUE
CLI_YELLOW = CLI.YELLOW
CLI_CYAN = CLI.CYAN
CLI_CLR = CLI.RESET

def fetch_active_nodes(source_node: str = None, model_filter: str = None) -> List[str]:
    """Fetch list of active participant nodes from current epoch.

    Args:
        source_node: Specific node to query (optional)
        model_filter: Only return nodes supporting this model (e.g. 'Qwen/Qwen3-235B-A22B-Instruct-2507-FP8')
    """
    # If source_node provided, try only that. Otherwise try random genesis nodes.
    sources = [source_node] if source_node else list(GENESIS_NODES)
    if not source_node:
        random.shuffle(sources)
        # Limit to checking 3 random genesis nodes to avoid long delays if all are down
        sources = sources[:3]

    for source in sources:
        if not source:
            continue

        try:
            # Handle full URL in source_node (e.g. from error message) or just base URL
            url = source if source.endswith("/participants") else f"{source}/v1/epochs/current/participants"

            response = requests.get(
                url,
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                # AICODE-NOTE: API returns {"active_participants": {"participants": [...]}}
                participants = data.get("active_participants", {}).get("participants", [])

                # Filter by model if specified
                if model_filter:
                    participants = [p for p in participants if model_filter in p.get("models", [])]

                nodes = [p.get("inference_url") for p in participants if p.get("inference_url")]
                if nodes:
                    if not source_node:
                        filter_msg = f" (filtered for {model_filter})" if model_filter else ""
                        print(f"{CLI_CYAN}✓ Fetched {len(nodes)} active nodes{filter_msg} from {source}{CLI_CLR}")
                    return nodes
        except Exception as e:
            if source_node: # Only print error if we were targeting a specific node
                print(f"{CLI_YELLOW}⚠ Could not fetch participants from {source}: {e}{CLI_CLR}")
            continue

    return []


def get_available_nodes(model_filter: str = "Qwen/Qwen3-235B-A22B-Instruct-2507-FP8") -> List[str]:
    """Get all available nodes: active participants filtered by model + genesis nodes as fallback.

    Args:
        model_filter: Only return nodes supporting this model
    """
    active = fetch_active_nodes(model_filter=model_filter)
    # Genesis nodes are gateways, not direct inference - only use as fallback
    if not active:
        print(f"{CLI_YELLOW}⚠ No active nodes found, falling back to genesis nodes{CLI_CLR}")
        return list(GENESIS_NODES)
    return active

