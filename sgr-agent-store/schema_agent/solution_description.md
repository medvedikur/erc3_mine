# NextStep SGR Agent

This solution improves upon the basic agent by introducing a structured "NextStep" schema and more complex reasoning capabilities.

## Structure
- `main.py`: Entry point with session statistics tracking.
- `agent.py`: Implements the enhanced agent logic.

## Features
- **Mental Checklist**: Enforces a structured thought process (Analyze, Plan, Verify).
- **Action Batching**: Can execute multiple store actions in a single turn.
- **Cost Tracking**: Uses `pricing.py` (from parent directory) to estimate token costs.
- **Improved Prompts**: Detailed system prompt with specific strategies for coupons and pagination.

