# Basic SGR Agent

This solution implements a basic Schema-Guided Reasoning (SGR) agent using OpenAI models.

## Structure
- `main.py`: Entry point using `erc3` library to fetch tasks and run the agent.
- `agent.py`: Contains the `run_agent` logic with a simple loop and parsing retry mechanism.

## Features
- Single-step reasoning (thought + function).
- Basic error handling for parsing failures.
- Direct integration with ERC3 store API.

