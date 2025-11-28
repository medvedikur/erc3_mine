import requests
import random
from typing import List

# === Genesis Nodes for Gonka Network ===
GENESIS_NODES = [
    "http://node1.gonka.ai:8000",
    "http://node2.gonka.ai:8000", 
    "http://node3.gonka.ai:8000",
    "http://185.216.21.98:8000",
    "http://47.236.26.199:8000",
    "http://47.236.19.22:18000",
    "http://gonka.spv.re:8000",
]

CLI_RED = "\x1B[31m"
CLI_GREEN = "\x1B[32m"
CLI_BLUE = "\x1B[34m"
CLI_YELLOW = "\x1B[33m"
CLI_CYAN = "\x1B[36m"
CLI_CLR = "\x1B[0m"

def fetch_active_nodes(source_node: str = None) -> List[str]:
    """Fetch list of active participant nodes from current epoch"""
    if source_node is None:
        source_node = random.choice(GENESIS_NODES)
    
    try:
        response = requests.get(
            f"{source_node}/v1/epochs/current/participants",
            timeout=10
        )
        if response.status_code == 200:
            data = response.json()
            participants = data.get("participants", [])
            nodes = [p.get("inference_url") for p in participants if p.get("inference_url")]
            if nodes:
                return nodes
    except Exception as e:
        print(f"{CLI_YELLOW}⚠ Could not fetch participants from {source_node}: {e}{CLI_CLR}")
    
    return []


def get_available_nodes() -> List[str]:
    """Get all available nodes: active participants + genesis nodes"""
    active = fetch_active_nodes()
    all_nodes = list(set(active + GENESIS_NODES))
    random.shuffle(all_nodes)
    return all_nodes

