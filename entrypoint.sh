#!/bin/bash
set -e

# ── Claude Code authentication ────────────────────────────────────────────────
# Option A (Railway/cloud): set ANTHROPIC_API_KEY — Claude CLI picks it up automatically
# Option B (local Docker): mount ~/.claude:/root/.claude:ro in docker-compose.yml
# Option C (any server): set CLAUDE_AUTH_JSON=<base64 of ~/.claude dir tarball>
#   Generate with: tar -czf - ~/.claude | base64 -w0

if [ -f /root/.claude/.credentials.json ]; then
    echo "[entrypoint] Found existing ~/.claude/.credentials.json (Railway Volume), skipping restore."
elif [ -n "$CLAUDE_AUTH_JSON" ]; then
    echo "[entrypoint] No credentials on volume, restoring from CLAUDE_AUTH_JSON..."
    mkdir -p /root/.claude
    CLEAN_JSON=$(echo "$CLAUDE_AUTH_JSON" | tr -d '"'"'" )
    if echo "$CLEAN_JSON" | base64 -d > /root/.claude/.credentials.json 2>/dev/null; then
        chmod 600 /root/.claude/.credentials.json
        echo "[entrypoint] Credentials restored from env var."
    else
        echo "[entrypoint] WARNING: CLAUDE_AUTH_JSON decode failed."
    fi
else
    echo "[entrypoint] WARNING: No credentials found. Claude Code will not work."
fi

# ── Start pipeline ────────────────────────────────────────────────────────────
exec python main.py
