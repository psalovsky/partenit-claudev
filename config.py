import os
from pathlib import Path


def _load_dotenv_file() -> None:
    """Load ``.env`` from project root so LLM_* / JIRA_* are set without exporting shell env."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    p = Path(__file__).resolve().parent / ".env"
    if p.is_file():
        load_dotenv(p)


_load_dotenv_file()

PORT = int(os.environ.get("PORT", 8090))
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "change-me")

# ── Orchestrator LLM (for classification, labeling, summarization) ────────────
# Any OpenAI-compatible API: DeepSeek, OpenAI, Groq, etc.
# For cheap tasks (parsing, classification) use a cheap model.
LLM_API_KEY = os.environ.get("LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL") or os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")

# Backward compat: old DEEPSEEK_* vars still work if LLM_* not set
DEEPSEEK_API_KEY = LLM_API_KEY
DEEPSEEK_BASE_URL = LLM_BASE_URL

# ── Pipeline worker LLM (heavy stages: PLAN, artifacts, dev, tests) ────────
# Default ``deepseek-chat`` via DeepSeek HTTP API. Historically this role was
# filled by Anthropic **Claude Code** CLI (Opus, agentic tools) — stronger on
# large refactors; consider restoring the commented CLI path in worker.py.
# Override endpoint/key if the worker should use a different provider than the orchestrator.
WORKER_LLM_API_KEY = (
    os.environ.get("WORKER_LLM_API_KEY", "").strip()
    or LLM_API_KEY
)
WORKER_LLM_BASE_URL = (
    os.environ.get("WORKER_LLM_BASE_URL", "").strip()
    or LLM_BASE_URL
)
WORKER_LLM_MODEL = os.environ.get("WORKER_LLM_MODEL", "deepseek-chat")
WORKER_MAX_TOKENS = int(os.environ.get("WORKER_MAX_TOKENS", "8192"))
WORKER_CONTEXT_MAX_BYTES = int(os.environ.get("WORKER_CONTEXT_MAX_BYTES", "100000"))

# Jira
JIRA_DOMAIN = os.environ["JIRA_DOMAIN"]
JIRA_EMAIL = os.environ["JIRA_EMAIL"]
JIRA_API_TOKEN = os.environ["JIRA_API_TOKEN"]
JIRA_PROJECT_KEY = os.environ.get("JIRA_PROJECT_KEY", "MYPROJECT")

# GitHub
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]            # pipeline repo
GITHUB_TOKEN_TARGET = os.environ.get(                 # target repo (clone + PR)
    "GITHUB_TOKEN_TARGET", os.environ["GITHUB_TOKEN"]
)
GITHUB_REPO = os.environ["GITHUB_REPO"]

# GitHub — secondary repo (fallback to main repo values)
GITHUB_REPO_BRIDGE = os.environ.get("GITHUB_REPO_BRIDGE", GITHUB_REPO)
GITHUB_TOKEN_BRIDGE = os.environ.get("GITHUB_TOKEN_BRIDGE", "") or GITHUB_TOKEN_TARGET

# Telegram
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Pipeline
TRIGGER_STATUS = os.environ.get("TRIGGER_STATUS", "In Progress")
STAGE_BRANCH = os.environ.get("STAGE_BRANCH", "stage")
MAX_CONCURRENT_JOBS = int(os.environ.get("MAX_CONCURRENT_JOBS", 3))
MAX_CONCURRENT_PIPELINES = int(os.environ.get("MAX_CONCURRENT_PIPELINES", 1))
JOB_TIMEOUT_MINUTES = int(os.environ.get("JOB_TIMEOUT_MINUTES", 60))
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", 3))
RETRY_DELAY_MINUTES = int(os.environ.get("RETRY_DELAY_MINUTES", 10))

# Auto-transition parent task when all subtasks are Done
# Set to "" to disable auto-transition
AUTO_TRANSITION_ON_COMPLETE = os.environ.get("AUTO_TRANSITION_ON_COMPLETE", "In Review")

# Planning pipeline: tasks with this prefix go through a different flow
# Worker LLM (default deepseek-chat) breaks the task into epics and subtasks
PLAN_PREFIX = os.environ.get("PLAN_PREFIX", "PLAN:")

# Bootstrap (greenfield) pipeline: tasks with this prefix initialize a new repo
BOOTSTRAP_PREFIX = os.environ.get("BOOTSTRAP_PREFIX", "BOOTSTRAP:")

# ── Jira status names (must match your Jira workflow exactly) ─────────────────
STATUS_CANCELLED = os.environ.get("STATUS_CANCELLED", "Cancelled")
STATUS_TODO = os.environ.get("STATUS_TODO", "To Do")
STATUS_IN_PROGRESS = os.environ.get("STATUS_IN_PROGRESS", "In Progress")
STATUS_DONE = os.environ.get("STATUS_DONE", "Done")
STATUS_READY_FOR_TEST = os.environ.get("STATUS_READY_FOR_TEST", "Ready for Test")
STATUS_IN_REVIEW = os.environ.get("STATUS_IN_REVIEW", "In Review")
STATUS_IN_TESTING = os.environ.get("STATUS_IN_TESTING", "In Testing")
STATUS_MERGE = os.environ.get("STATUS_MERGE", "Ready to Merge")

# After BOOTSTRAP pipeline completes, move parent to this status (optional).
# Set to "" to disable.
AUTO_TRANSITION_ON_BOOTSTRAP_COMPLETE = os.environ.get(
    "AUTO_TRANSITION_ON_BOOTSTRAP_COMPLETE",
    "Ready for Development",
)

# ── Pipeline stage labels (applied to Jira sub-tasks) ─────────────────────────
PIPELINE_LABEL_PREFIX = "pipeline:"
STAGE_SYS_ANALYSIS = "sys-analysis"    # Worker LLM → SYSTEM_ANALYSIS.md (ex-Claude Code)
STAGE_ARCHITECTURE = "architecture"     # Worker LLM → ARCHITECTURE_DECISION.md
STAGE_DEVELOPMENT = "development"       # Worker LLM → code / PR
STAGE_TESTING = "testing"              # Worker LLM → tests

STAGE_BOOTSTRAP_PRODUCT_FRAMING = "bootstrap-product-framing"
STAGE_BOOTSTRAP_ARCH_BASELINE = "bootstrap-architecture-baseline"
STAGE_BOOTSTRAP_REPO_SCAFFOLD = "bootstrap-repo-scaffold"
STAGE_BOOTSTRAP_WORK_BREAKDOWN = "bootstrap-work-breakdown"

ALL_STAGES = [STAGE_SYS_ANALYSIS, STAGE_ARCHITECTURE, STAGE_DEVELOPMENT, STAGE_TESTING]
BOOTSTRAP_STAGES = [
    STAGE_BOOTSTRAP_PRODUCT_FRAMING,
    STAGE_BOOTSTRAP_ARCH_BASELINE,
    STAGE_BOOTSTRAP_REPO_SCAFFOLD,
    STAGE_BOOTSTRAP_WORK_BREAKDOWN,
]

# Stages that can start simultaneously (no prerequisites)
STAGE_PREREQUISITES: dict[str, list[str]] = {
    STAGE_SYS_ANALYSIS: [],
    STAGE_ARCHITECTURE: [],
    STAGE_DEVELOPMENT: [STAGE_SYS_ANALYSIS, STAGE_ARCHITECTURE],
    STAGE_TESTING: [STAGE_DEVELOPMENT],
    # Bootstrap flow (greenfield) — sequential
    STAGE_BOOTSTRAP_PRODUCT_FRAMING: [],
    STAGE_BOOTSTRAP_ARCH_BASELINE: [STAGE_BOOTSTRAP_PRODUCT_FRAMING],
    STAGE_BOOTSTRAP_REPO_SCAFFOLD: [STAGE_BOOTSTRAP_ARCH_BASELINE],
    STAGE_BOOTSTRAP_WORK_BREAKDOWN: [STAGE_BOOTSTRAP_REPO_SCAFFOLD],
}

# Directory for analysis/architecture artifacts (relative to repo root)
ARTIFACTS_DIR = os.environ.get("ARTIFACTS_DIR", "docs/decisions")

# Stages that write artifacts to Jira (markdown), not code
ARTIFACT_STAGES = {
    STAGE_SYS_ANALYSIS,
    STAGE_ARCHITECTURE,
    STAGE_BOOTSTRAP_PRODUCT_FRAMING,
    STAGE_BOOTSTRAP_ARCH_BASELINE,
}
# Stages that push code to GitHub
CODE_STAGES = {STAGE_DEVELOPMENT, STAGE_TESTING, STAGE_BOOTSTRAP_REPO_SCAFFOLD}
