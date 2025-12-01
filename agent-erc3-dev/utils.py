import requests
import random
from typing import List

# === Genesis Nodes for Gonka Network ===
GENESIS_NODES = [
    "http://node1.gonka.ai:8000",
    "http://node2.gonka.ai:8000", 
    #"http://node3.gonka.ai:8000",
    "http://185.216.21.98:8000",
    "http://47.236.26.199:8000",
    #"http://47.236.19.22:18000",
    "http://gonka.spv.re:8000",
    "http://85.234.91.172:8000",
]

CLI_RED = "\x1B[31m"
CLI_GREEN = "\x1B[32m"
CLI_BLUE = "\x1B[34m"
CLI_YELLOW = "\x1B[33m"
CLI_CYAN = "\x1B[36m"
CLI_CLR = "\x1B[0m"

def fetch_active_nodes(source_node: str = None) -> List[str]:
    """Fetch list of active participant nodes from current epoch"""
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
                participants = data.get("participants", [])
                nodes = [p.get("inference_url") for p in participants if p.get("inference_url")]
                if nodes:
                    if not source_node:
                         print(f"{CLI_CYAN}✓ Fetched {len(nodes)} active nodes from {source}{CLI_CLR}")
                    return nodes
        except Exception as e:
            if source_node: # Only print error if we were targeting a specific node
                print(f"{CLI_YELLOW}⚠ Could not fetch participants from {source}: {e}{CLI_CLR}")
            continue
    
    return []


def get_available_nodes() -> List[str]:
    """Get all available nodes: active participants + genesis nodes"""
    active = fetch_active_nodes()
    all_nodes = list(set(active + GENESIS_NODES))
    random.shuffle(all_nodes)
    return all_nodes

