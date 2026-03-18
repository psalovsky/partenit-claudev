import os

PORT = int(os.environ.get("PORT", 8090))
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "change-me")

# ── Orchestrator LLM (for classification, labeling, summarization) ────────────
# Any OpenAI-compatible API: DeepSeek, OpenAI, Anthropic via proxy, Ollama, etc.
# For cheap tasks (parsing, classification) use a cheap model.
# Claude Code handles all the actual coding work separately.
LLM_API_KEY = os.environ.get("LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL") or os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")

# Backward compat: old DEEPSEEK_* vars still work if LLM_* not set
DEEPSEEK_API_KEY = LLM_API_KEY
DEEPSEEK_BASE_URL = LLM_BASE_URL

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
# Instead of coding, Claude Code breaks the task into epics and subtasks
PLAN_PREFIX = os.environ.get("PLAN_PREFIX", "PLAN:")

# ── Jira status names (must match your Jira workflow exactly) ─────────────────
STATUS_CANCELLED = os.environ.get("STATUS_CANCELLED", "Cancelled")
STATUS_TODO = os.environ.get("STATUS_TODO", "To Do")
STATUS_IN_PROGRESS = os.environ.get("STATUS_IN_PROGRESS", "In Progress")
STATUS_DONE = os.environ.get("STATUS_DONE", "Done")
STATUS_READY_FOR_TEST = os.environ.get("STATUS_READY_FOR_TEST", "Ready for Test")
STATUS_IN_REVIEW = os.environ.get("STATUS_IN_REVIEW", "In Review")
STATUS_IN_TESTING = os.environ.get("STATUS_IN_TESTING", "In Testing")
STATUS_MERGE = os.environ.get("STATUS_MERGE", "Ready to Merge")

# ── Pipeline stage labels (applied to Jira sub-tasks) ─────────────────────────
PIPELINE_LABEL_PREFIX = "pipeline:"
STAGE_SYS_ANALYSIS = "sys-analysis"    # Claude Code: reads code → SYSTEM_ANALYSIS.md
STAGE_ARCHITECTURE = "architecture"     # Claude Code: designs solution → ARCHITECTURE_DECISION.md
STAGE_DEVELOPMENT = "development"       # Claude Code: writes code → PR to stage
STAGE_TESTING = "testing"              # Claude Code: writes tests → push to dev branch

ALL_STAGES = [STAGE_SYS_ANALYSIS, STAGE_ARCHITECTURE, STAGE_DEVELOPMENT, STAGE_TESTING]

# Stages that can start simultaneously (no prerequisites)
STAGE_PREREQUISITES: dict[str, list[str]] = {
    STAGE_SYS_ANALYSIS: [],
    STAGE_ARCHITECTURE: [],
    STAGE_DEVELOPMENT: [STAGE_SYS_ANALYSIS, STAGE_ARCHITECTURE],
    STAGE_TESTING: [STAGE_DEVELOPMENT],
}

# Stages that write artifacts to Jira (markdown), not code
ARTIFACT_STAGES = {STAGE_SYS_ANALYSIS, STAGE_ARCHITECTURE}
# Stages that push code to GitHub
CODE_STAGES = {STAGE_DEVELOPMENT, STAGE_TESTING}
