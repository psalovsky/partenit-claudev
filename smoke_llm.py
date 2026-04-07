"""One-shot check: DeepSeek (or WORKER_LLM_*) responds. Run: python smoke_llm.py"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx


def main() -> int:
    try:
        from dotenv import load_dotenv
    except ImportError:
        print("Install: pip install python-dotenv httpx")
        return 1

    load_dotenv(Path(__file__).resolve().parent / ".env")

    import os

    key = (
        os.environ.get("LLM_API_KEY")
        or os.environ.get("DEEPSEEK_API_KEY")
        or os.environ.get("WORKER_LLM_API_KEY")
        or ""
    ).strip()
    base = (
        os.environ.get("WORKER_LLM_BASE_URL")
        or os.environ.get("LLM_BASE_URL")
        or "https://api.deepseek.com"
    ).rstrip("/")
    model = os.environ.get("WORKER_LLM_MODEL") or os.environ.get("LLM_MODEL") or "deepseek-chat"

    if not key:
        print("FAIL: нет ключа. Добавь в .env: LLM_API_KEY=sk-...")
        return 1

    url = f"{base}/v1/chat/completions"
    r = httpx.post(
        url,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
            "max_tokens": 32,
            "temperature": 0,
        },
        timeout=90,
    )
    print("HTTP", r.status_code, "|", base, "| model:", model)
    if not r.is_success:
        print(r.text[:800])
        return 1
    data = r.json()
    text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    print("assistant:", repr(text.strip()[:500]))
    print("usage:", data.get("usage"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
