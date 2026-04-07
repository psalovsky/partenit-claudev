#!/bin/bash
set -e

# Worker: DeepSeek-compatible HTTP API (default model deepseek-chat).
# Legacy Claude Code CLI auth block removed — see worker.py commented ``_run_claude_cli_legacy``.

exec python main.py
