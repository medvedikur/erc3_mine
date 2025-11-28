# Gonka Network SGR Agent

This solution runs the SGR agent on the Gonka Network, a decentralized inference network.

## Structure
- `main.py`: Entry point handling Gonka connection, failover, and failure logging.
- `agent.py`: Agent logic adapted for Gonka (Qwen models) and decentralized environment.
- `logs/`: Directory for storing failure logs and conversation traces.

## Features
- **Decentralized Inference**: Connects to Gonka nodes using `gonka_openai`.
- **Node Failover**: Automatically switches nodes if one fails or times out.
- **Failure Logging**: Detailed JSON and text logs for failed tasks to aid debugging.
- **Qwen 235B Model**: Optimized for the specific model hosted on Gonka.
- **Cost Estimation**: Maps Gonka models to OpenRouter equivalents for pricing estimation.

