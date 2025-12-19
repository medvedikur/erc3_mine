"""
Configuration for ERC3 Agent.

Edit this file to change benchmark, models, and other settings.
"""

# ═══════════════════════════════════════════════════════════════════════════════
# BENCHMARK SETTINGS
# ═══════════════════════════════════════════════════════════════════════════════

# Benchmark type: "erc3-test" (24 tasks), "erc3-dev" (dev tasks), "erc3" (production)
BENCHMARK = "erc3-test"

# Workspace name (for organizing sessions)
WORKSPACE = "test-workspace-1"

# Session name prefix
SESSION_NAME = "@mishka ERC3-Test Agent"

# Competition flags
#COMPETITION_FLAGS = ["compete_accuracy", "compete_budget", "compete_speed", "compete_local"]
COMPETITION_FLAGS = ["compete_budget", "compete_speed", "compete_local"]

# API base URL
API_BASE_URL = "https://erc.timetoact-group.at"


# ═══════════════════════════════════════════════════════════════════════════════
# MODEL SETTINGS (defaults, can be overridden by env vars)
# ═══════════════════════════════════════════════════════════════════════════════

# Gonka Network model
DEFAULT_MODEL_GONKA = "qwen/qwen3-30b-a3b-instruct-2507"

# OpenRouter model
DEFAULT_MODEL_OPENROUTER = "qwen/qwen3-235b-a22b-2507"
#DEFAULT_MODEL_OPENROUTER = "qwen/qwen3-30b-a3b-instruct-2507"


# Pricing model ID (for cost calculation)
DEFAULT_PRICING_MODEL = "qwen/qwen3-235b-a22b"


# ═══════════════════════════════════════════════════════════════════════════════
# AGENT SETTINGS
# ═══════════════════════════════════════════════════════════════════════════════

# Max turns per task before giving up
MAX_TURNS_PER_TASK = 20

# Default number of threads for parallel mode
DEFAULT_THREADS = 1

# Retry attempts for LLM calls
LLM_RETRY_ATTEMPTS = 3


# ═══════════════════════════════════════════════════════════════════════════════
# LOGGING SETTINGS
# ═══════════════════════════════════════════════════════════════════════════════

# Directory for logs
LOGS_DIR = "logs"

# Directory for test logs (when running with -tests_on)
LOGS_DIR_TESTS = "logs_tests"

# Directory for wiki cache
WIKI_DUMP_DIR = "wiki_dump"

# Directory for test wiki cache
WIKI_DUMP_DIR_TESTS = "wiki_dump_tests"
