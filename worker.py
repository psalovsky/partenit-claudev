import json
import os
import shutil
import subprocess
import time
import logging

import httpx

from orchestrator import analyze_result, suggest_labels
from telegram_notifier import (
    notify_pipeline_started,
    notify_subtasks_created,
    notify_stage_started,
    notify_artifact_done,
    notify_pr_created,
    notify_testing_done,
    notify_all_done,
    notify_merged,
    notify_error,
)
from jira_client import JiraClient
from github_client import GitHubClient
from dependency_tracker import (
    collect_artifact_context,
    trigger_next_stages,
    all_stages_done,
)
from prompts import build_stage_prompt
from config import (
    GITHUB_TOKEN_TARGET,
    GITHUB_REPO,
    GITHUB_TOKEN_BRIDGE,
    GITHUB_REPO_BRIDGE,
    STAGE_BRANCH,
    JOB_TIMEOUT_MINUTES,
    MAX_RETRIES,
    RETRY_DELAY_MINUTES,
    ARTIFACT_STAGES,
    CODE_STAGES,
    STATUS_DONE,
    STATUS_IN_REVIEW,
    STATUS_IN_PROGRESS,
    STATUS_MERGE,
    JIRA_PROJECT_KEY,
    PIPELINE_LABEL_PREFIX,
    ALL_STAGES,
    STAGE_PREREQUISITES,
    AUTO_TRANSITION_ON_COMPLETE,
    AUTO_TRANSITION_ON_BOOTSTRAP_COMPLETE,
    BOOTSTRAP_PREFIX,
    BOOTSTRAP_STAGES,
    STAGE_BOOTSTRAP_WORK_BREAKDOWN,
    WORKER_LLM_API_KEY,
    WORKER_LLM_BASE_URL,
    WORKER_LLM_MODEL,
    WORKER_MAX_TOKENS,
    WORKER_CONTEXT_MAX_BYTES,
)

logger = logging.getLogger("pipeline.worker")
jira = JiraClient()
github = GitHubClient()


# ── Repo routing ──────────────────────────────────────────────────────────────

def _get_repo_config(job: dict) -> dict:
    """Return {"repo": ..., "token": ...} based on job labels.

    If the job has label ``repo:bridge`` → secondary repo/token,
    otherwise the default target repo/token.
    """
    labels = job.get("labels", [])
    if "repo:bridge" in labels:
        return {"repo": GITHUB_REPO_BRIDGE, "token": GITHUB_TOKEN_BRIDGE}
    return {"repo": GITHUB_REPO, "token": GITHUB_TOKEN_TARGET}


def _github_for_repo(repo_cfg: dict) -> GitHubClient:
    """Return a GitHubClient configured for the given repo/token."""
    client = GitHubClient()
    client.repo = repo_cfg["repo"]
    client.token = repo_cfg["token"]
    client.headers = {
        "Authorization": f"Bearer {repo_cfg['token']}",
        "Accept": "application/vnd.github.v3+json",
    }
    return client


# ── Helpers ───────────────────────────────────────────────────────────────────

def _clone_repo(work_dir: str, branch_name: str, repo_cfg: dict | None = None) -> None:
    """Clone repo and create a new local branch (no remote tracking)."""
    if repo_cfg is None:
        repo_cfg = {"repo": GITHUB_REPO, "token": GITHUB_TOKEN_TARGET}
    repo_url = f"https://x-access-token:{repo_cfg['token']}@github.com/{repo_cfg['repo']}.git"
    result = subprocess.run(
        ["git", "clone", "--depth=1", repo_url, work_dir],
        capture_output=True,
        timeout=120,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace").strip()
        raise Exception(f"git clone failed (rc={result.returncode}): {stderr[:400]}")
    subprocess.run(
        ["git", "checkout", "-b", branch_name],
        cwd=work_dir,
        check=True,
        capture_output=True,
    )


def _clone_repo_with_branch(work_dir: str, branch_name: str, repo_cfg: dict | None = None) -> None:
    """Clone repo; if branch_name exists on remote, check it out.
    Otherwise create a new branch. This allows code stages to pick up
    artifacts committed by earlier artifact stages."""
    if repo_cfg is None:
        repo_cfg = {"repo": GITHUB_REPO, "token": GITHUB_TOKEN_TARGET}
    repo_url = (
        f"https://x-access-token:{repo_cfg['token']}"
        f"@github.com/{repo_cfg['repo']}.git"
    )
    # Try cloning the existing branch first
    result = subprocess.run(
        ["git", "clone", "--depth=50", "-b", branch_name, repo_url, work_dir],
        capture_output=True,
        timeout=120,
    )
    if result.returncode == 0:
        logger.info("Cloned existing branch %s", branch_name)
        return
    # Branch doesn't exist on remote — clone default and create it
    result = subprocess.run(
        ["git", "clone", "--depth=1", repo_url, work_dir],
        capture_output=True,
        timeout=120,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace").strip()
        raise Exception(f"git clone failed (rc={result.returncode}): {stderr[:400]}")
    subprocess.run(
        ["git", "checkout", "-b", branch_name],
        cwd=work_dir,
        check=True,
        capture_output=True,
    )


# Errors that should trigger a retry (transient / server-side)
_RETRYABLE_MARKERS = (
    # Rate limits
    "rate limit", "429", "overloaded", "exceeded your current quota",
    # Server errors
    "500", "internal server error", "api_error",
    "502", "bad gateway",
    "503", "service unavailable",
    "529", "overloaded",
    # Auth / transient API errors (retry)
    "401", "authentication_error", "token has expired",
    # Network / transient
    "connection error", "timeout", "econnreset", "econnrefused",
    "socket hang up", "fetch failed",
)

# Longer delay for rate limits, shorter for server errors
_RATE_LIMIT_MARKERS = ("rate limit", "429", "overloaded", "exceeded your current quota")

# ── Repo snapshot for API-only worker (no agentic file tools) ───────────────
_SKIP_CONTEXT_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
    ".tox", ".mypy_cache", ".pytest_cache", "target",
}


def _build_repo_context_prompt(work_dir: str) -> str:
    """File tree + key docs → text prepended to the stage prompt."""
    max_bytes = WORKER_CONTEXT_MAX_BYTES
    parts: list[str] = []
    used = 0

    def append(chunk: str) -> bool:
        nonlocal used
        b = len(chunk.encode("utf-8"))
        if used + b > max_bytes:
            return False
        parts.append(chunk)
        used += b
        return True

    paths: list[str] = []
    for root, dirs, files in os.walk(work_dir, topdown=True):
        dirs[:] = [
            d for d in dirs
            if d not in _SKIP_CONTEXT_DIRS and not d.startswith(".")
        ]
        for f in files:
            if f.startswith("."):
                continue
            rel = os.path.relpath(os.path.join(root, f), work_dir)
            paths.append(rel.replace("\\", "/"))
    paths.sort()
    head = paths[:800]
    tree_block = (
        f"## Repository file list ({len(paths)} files, showing {len(head)})\n"
        f"```\n{chr(10).join(head)}\n```\n"
    )
    if not append(tree_block):
        return "\n\n".join(parts)

    for name in (
        # Root docs (legacy)
        "CLAUDE.md", "README.md", "ARCHITECTURE.md", "STEERING.md",
        # Bootstrap docs (greenfield)
        "docs/product/PRODUCT_BRIEF.md",
        "docs/architecture/ARCHITECTURE.md",
        "docs/governance/STEERING.md",
        "docs/governance/CLAUDE.md",
        # Package metadata
        "package.json", "pyproject.toml", "requirements.txt",
    ):
        fp = os.path.join(work_dir, name)
        if not os.path.isfile(fp):
            continue
        try:
            with open(fp, encoding="utf-8", errors="replace") as fh:
                text = fh.read(50_000)
        except OSError:
            continue
        block = f"## File: {name}\n```\n{text}\n```\n"
        if not append(block):
            break

    return "\n\n".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# LEGACY: Anthropic **Claude Code** CLI (model ``claude-opus-4-6``)
# ─────────────────────────────────────────────────────────────────────────────
# Agentic coding with local tools; stronger than a single chat completion on
# hard tasks. To switch back: implement a runner that calls the block below
# instead of ``_run_worker_llm_api`` from ``_run_claude_with_retry``, and
# restore Node + ``npm i -g @anthropic-ai/claude-code`` in the Dockerfile.
#
# def _run_claude_cli_legacy(prompt: str, work_dir: str, job: dict):
#     try:
#         from refresh_token import main as _refresh_token
#         _refresh_token()
#     except Exception:
#         pass
#     try:
#         subprocess.run(
#             ["claude", "-p", "ok", "--max-turns", "1", "--output-format", "text"],
#             cwd=work_dir, capture_output=True, text=True, timeout=30,
#         )
#     except Exception:
#         pass
#     proc = subprocess.Popen(
#         [
#             "claude", "-p", prompt,
#             "--model", "claude-opus-4-6",
#             "--output-format", "text",
#             "--max-turns", "50",
#         ],
#         cwd=work_dir,
#         stdout=subprocess.PIPE,
#         stderr=subprocess.PIPE,
#         text=True,
#     )
#     job["process"] = proc
#     try:
#         stdout, stderr = proc.communicate(timeout=JOB_TIMEOUT_MINUTES * 60)
#     except subprocess.TimeoutExpired:
#         proc.kill()
#         stdout, stderr = proc.communicate()
#         raise Exception(f"Claude Code timed out after {JOB_TIMEOUT_MINUTES}m")
#     finally:
#         job.pop("process", None)
#     return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)
# ─────────────────────────────────────────────────────────────────────────────


def _assistant_text_from_completion(data: dict) -> str:
    choices = data.get("choices") or []
    if not choices:
        raise ValueError("no choices in completion response")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        bits: list[str] = []
        for p in content:
            if isinstance(p, dict) and p.get("type") == "text":
                bits.append(str(p.get("text", "")))
            elif isinstance(p, str):
                bits.append(p)
        return "".join(bits)
    return str(content)


def _run_worker_llm_api(prompt: str, work_dir: str, job: dict) -> subprocess.CompletedProcess:
    """Single-shot chat completion for pipeline stages (default: **deepseek-chat**).

    Uses ``WORKER_LLM_*`` env (see config). For stronger results prefer larger
    DeepSeek/OpenAI-compatible models or restore **Claude Code** (commented legacy above).
    """
    if job.get("cancelled"):
        return subprocess.CompletedProcess(["worker-llm"], 1, "", "Cancelled")

    if not (WORKER_LLM_API_KEY or "").strip():
        return subprocess.CompletedProcess(
            ["worker-llm"], 1, "",
            "WORKER_LLM_API_KEY / LLM_API_KEY is not set",
        )

    ctx = _build_repo_context_prompt(work_dir)
    if ctx:
        full_prompt = (
            "The following is a snapshot of the cloned repository (paths + key files). "
            "Use it as context.\n\n"
            + ctx
            + "\n\n---\n\n## Task for you\n\n"
            + prompt
        )
    else:
        full_prompt = prompt

    url = f"{WORKER_LLM_BASE_URL.rstrip('/')}/v1/chat/completions"
    timeout_sec = float(JOB_TIMEOUT_MINUTES) * 60
    payload = {
        "model": WORKER_LLM_MODEL,
        "messages": [{"role": "user", "content": full_prompt}],
        "max_tokens": WORKER_MAX_TOKENS,
        "temperature": 0.2,
    }
    headers = {
        "Authorization": f"Bearer {WORKER_LLM_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=timeout_sec) as client:
            r = client.post(url, headers=headers, json=payload)
            if r.is_success:
                data = r.json()
                text = _assistant_text_from_completion(data)
                usage = data.get("usage")
                if usage:
                    logger.info(
                        "[%s] worker LLM usage: %s",
                        job.get("issue_key", "?"),
                        json.dumps(usage, ensure_ascii=False),
                    )
                return subprocess.CompletedProcess(["worker-llm"], 0, text, "")
            err_body = r.text[:2000]
            return subprocess.CompletedProcess(
                ["worker-llm"], 1, "",
                f"HTTP {r.status_code}: {err_body}",
            )
    except httpx.TimeoutException as e:
        return subprocess.CompletedProcess(
            ["worker-llm"], 1, "",
            f"Worker LLM timed out after {JOB_TIMEOUT_MINUTES}m: {e}",
        )
    except Exception as e:
        return subprocess.CompletedProcess(["worker-llm"], 1, "", str(e))


def _sleep_interruptible(seconds: int, job: dict) -> None:
    """Sleep in 5s chunks, aborting early if job is cancelled."""
    end = time.time() + seconds
    while time.time() < end:
        if job.get("cancelled"):
            raise Exception("Cancelled during retry wait")
        time.sleep(5)


def _run_claude_with_retry(prompt: str, work_dir: str, job: dict) -> subprocess.CompletedProcess:
    """Run pipeline worker LLM (default **deepseek-chat**) with retries.

    Name kept for call sites. Former implementation: Anthropic **Claude Code** CLI
    (see commented ``_run_claude_cli_legacy`` above) — prefer that or a larger
    API model when quality is insufficient.

    Retries on: rate limits (429), server errors, network issues.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        if job.get("cancelled"):
            raise Exception("Cancelled")
        result = _run_worker_llm_api(prompt, work_dir, job)
        if result.returncode == 0:
            return result

        combined = ((result.stdout or "") + (result.stderr or "")).lower()
        is_retryable = any(m in combined for m in _RETRYABLE_MARKERS)
        is_rate_limit = any(m in combined for m in _RATE_LIMIT_MARKERS)

        if is_retryable and attempt < MAX_RETRIES:
            if is_rate_limit:
                delay_min = RETRY_DELAY_MINUTES
                reason = "Rate limit"
            else:
                delay_min = min(RETRY_DELAY_MINUTES, 2)
                reason = "Server error"

            logger.warning(
                "[%s] %s, attempt %d/%d, waiting %dm",
                job["issue_key"], reason, attempt, MAX_RETRIES, delay_min,
            )
            notify_error(
                job["issue_key"], job.get("stage", "?"),
                f"{reason} — retry {attempt}/{MAX_RETRIES} in {delay_min}m",
                job.get("jira_domain", ""),
            )
            _sleep_interruptible(delay_min * 60, job)
            continue

        out = (result.stdout or "")[:300]
        err = (result.stderr or "")[:300]
        raise Exception(
            f"Worker LLM ({WORKER_LLM_MODEL}) rc={result.returncode}\n"
            f"stdout: {out}\nstderr: {err}"
        )
    raise Exception(f"Worker LLM failed after {MAX_RETRIES} retries")


def _git_changed_files(work_dir: str) -> list[str]:
    diff = subprocess.run(
        ["git", "diff", "--name-only"],
        cwd=work_dir, capture_output=True, text=True,
    )
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=work_dir, capture_output=True, text=True,
    )
    return [f for f in (diff.stdout + untracked.stdout).strip().split("\n") if f]


def _relaunch_subtask(sub: dict, parent_key: str, stage: str) -> None:
    """Re-launch a dead subtask (In Progress but no active job).

    Creates a new job directly without waiting for webhook.
    """
    import uuid
    import time as _time

    sub_key = sub["key"]
    job_id = str(uuid.uuid4())[:8]

    # Fetch full subtask info for the job dict
    try:
        full = jira.get_issue(sub_key)
        full_fields = full.get("fields", {})
    except Exception:
        full_fields = {}

    parent_ref = full_fields.get("parent", {})
    job = {
        "job_id": job_id,
        "issue_key": sub_key,
        "key": sub_key,
        "parent_key": parent_ref.get("key", parent_key),
        "summary": full_fields.get("summary", sub.get("summary", "")),
        "description": full_fields.get("description", {}),
        "description_text": "",
        "issue_type": full_fields.get("issuetype", {}).get("name", "Sub-task"),
        "stage": stage,
        "trigger": "In Progress",
        "jira_domain": f"{os.environ.get('JIRA_DOMAIN', '')}.atlassian.net",
        "priority": full_fields.get("priority", {}).get("name", "Medium"),
        "labels": full_fields.get("labels", sub.get("labels", [])),
        "components": [
            c.get("name", "") if isinstance(c, dict) else c
            for c in full_fields.get("components", [])
        ],
        "status": "queued",
        "created": _time.time(),
    }

    from main import _launch_job, active_pipelines
    # Ensure parent is in active pipelines
    active_pipelines.add(parent_key)
    _launch_job(job)
    logger.info("[%s] re-launched dead stage %s as job %s", parent_key, stage, job_id)


# ── Planning job: break feature into epics and tasks ─────────────────────────

def run_plan_job(job: dict) -> None:
    """When a PLAN: task moves to In Progress: worker LLM (default deepseek-chat)
    uses repo snapshot + prompt to break the feature into epics/tasks in Jira.

    Flow:
    1. Clone repo
    2. Worker LLM analyzes snapshot + task description (ex-**Claude Code** path)
    3. Outputs JSON with epics and tasks
    4. Pipeline creates epics and tasks in Jira
    5. Original task → Done
    """
    _ensure_description_text(job)
    issue_key = job["issue_key"]
    job_id = job["job_id"]
    work_dir = f"/tmp/pipeline-work/{job_id}"

    try:
        jira_domain = job.get("jira_domain", "")
        from telegram_notifier import _send
        _send(
            f"📝 <b>Planning started</b>\n"
            f"Task: <a href='https://{jira_domain}/browse/{issue_key}'>{issue_key}</a>\n"
            f"{job['summary']}\n"
            f"Worker LLM ({WORKER_LLM_MODEL}) is analyzing the codebase..."
        )

        jira.add_comment(
            issue_key,
            f"🤖 Planning started. Worker LLM ({WORKER_LLM_MODEL}) is reading the codebase "
            f"and breaking down the feature into epics and tasks.\n"
            f"Job: {job_id}",
        )

        from prompts import build_plan_prompt
        prompt = build_plan_prompt(job)

        repo_cfg = _get_repo_config(job)
        os.makedirs(work_dir, exist_ok=True)
        _clone_repo(work_dir, f"plan/{issue_key.lower()}", repo_cfg)

        start = time.time()
        if job.get("cancelled"):
            raise Exception("Cancelled")

        result = _run_claude_with_retry(prompt, work_dir, job)
        duration = int(time.time() - start)

        if result.returncode != 0:
            raise Exception(
                f"Worker LLM rc={result.returncode}: {result.stderr[:500]}"
            )

        # Parse the JSON output
        output = result.stdout.strip()
        # Try to extract JSON (model may wrap it in prose)
        json_start = output.find("{")
        json_end = output.rfind("}") + 1
        if json_start == -1 or json_end == 0:
            raise Exception("Worker LLM did not output valid JSON")

        plan = json.loads(output[json_start:json_end])

        # Check if business analysis rejected the feature
        if plan.get("rejected"):
            reason = plan.get("reason", "No reason provided.")
            jira.add_comment(
                issue_key,
                f"🤖 **Feature rejected after business analysis.**\n\n"
                f"{reason}\n\n"
                f"No epics or tasks were created.\n"
                f"⏱ {duration // 60}m {duration % 60}s | Job: {job_id}",
            )
            jira.transition(issue_key, STATUS_DONE)
            _send(
                f"🚫 <b>Feature rejected</b>\n"
                f"<a href='https://{jira_domain}/browse/{issue_key}'>{issue_key}</a>\n"
                f"{reason[:200]}"
            )
            logger.info("[%s] Plan rejected: %s", issue_key, reason[:200])
            return

        epics = plan.get("epics", [])

        if not epics:
            jira.add_comment(
                issue_key,
                "🤖 Worker LLM could not break this down into epics. "
                "Try adding more detail to the task description.",
            )
            jira.transition(issue_key, STATUS_DONE)
            return

        # Create epics and tasks in Jira
        from config import JIRA_PROJECT_KEY
        created_epics = []
        total_tasks = 0

        for epic_data in epics:
            epic_title = epic_data.get("title", "Untitled epic")
            epic_desc = epic_data.get("description", "")
            tasks = epic_data.get("tasks", [])

            # Create epic
            epic_body = {
                "fields": {
                    "project": {"key": JIRA_PROJECT_KEY},
                    "summary": epic_title,
                    "description": {
                        "version": 1,
                        "type": "doc",
                        "content": [{
                            "type": "paragraph",
                            "content": [{"type": "text", "text": epic_desc}],
                        }],
                    },
                    "issuetype": {"name": "Epic"},
                }
            }
            import httpx as _httpx
            r = _httpx.post(
                f"{jira.base_url}/rest/api/3/issue",
                headers=jira.headers,
                json=epic_body,
                timeout=10,
            )
            if not r.is_success:
                logger.warning("[%s] Failed to create epic '%s': %s",
                               issue_key, epic_title, r.text[:200])
                continue

            epic_key = r.json()["key"]
            created_epics.append({"key": epic_key, "title": epic_title, "tasks": []})

            # Create tasks under this epic
            for task_data in tasks:
                task_title = task_data.get("title", "Untitled task")
                task_desc = task_data.get("description", "")
                task_labels = task_data.get("labels", [])

                task_body = {
                    "fields": {
                        "project": {"key": JIRA_PROJECT_KEY},
                        "parent": {"key": epic_key},
                        "summary": task_title,
                        "description": {
                            "version": 1,
                            "type": "doc",
                            "content": [{
                                "type": "paragraph",
                                "content": [{"type": "text", "text": task_desc}],
                            }],
                        },
                        "issuetype": {"name": "Task"},
                        "labels": task_labels,
                    }
                }
                r = _httpx.post(
                    f"{jira.base_url}/rest/api/3/issue",
                    headers=jira.headers,
                    json=task_body,
                    timeout=10,
                )
                if r.is_success:
                    task_key = r.json()["key"]
                    created_epics[-1]["tasks"].append(task_key)
                    total_tasks += 1
                else:
                    logger.warning("[%s] Failed to create task '%s': %s",
                                   issue_key, task_title, r.text[:200])

        # Build summary comment
        summary_lines = [
            f"🤖 **Planning complete** — {len(created_epics)} epics, "
            f"{total_tasks} tasks created.\n",
        ]
        for epic in created_epics:
            epic_url = f"https://{jira_domain}/browse/{epic['key']}"
            summary_lines.append(
                f"### [{epic['key']}]({epic_url}): {epic['title']}"
            )
            for task_key in epic["tasks"]:
                task_url = f"https://{jira_domain}/browse/{task_key}"
                summary_lines.append(f"  - [{task_key}]({task_url})")
            summary_lines.append("")

        summary_lines.append(
            f"⏱ {duration // 60}m {duration % 60}s | Job: {job_id}\n\n"
            "Move any task to **In Progress** to start the dev pipeline."
        )

        jira.add_comment(issue_key, "\n".join(summary_lines))
        jira.transition(issue_key, STATUS_DONE)

        _send(
            f"📝 <b>Planning complete!</b>\n"
            f"<a href='https://{jira_domain}/browse/{issue_key}'>{issue_key}</a>\n"
            f"{len(created_epics)} epics, {total_tasks} tasks\n"
            f"⏱ {duration // 60}m {duration % 60}s"
        )
        logger.info("[%s] Plan complete: %d epics, %d tasks (%ds)",
                    issue_key, len(created_epics), total_tasks, duration)

    except Exception as e:
        logger.error("[%s] plan FAIL: %s", issue_key, e)
        notify_error(issue_key, "planning", str(e), job.get("jira_domain", ""))
        try:
            jira.add_comment(
                issue_key,
                f"❌ Planning error: {str(e)[:500]}\nJob: {job_id}",
            )
        except Exception:
            pass
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# ── Setup job: create pipeline subtasks for a parent task ────────────────────

_STAGE_SUMMARIES = {
    "sys-analysis":  "System Analysis",
    "architecture":  "Architecture Decision",
    "development":   "Development",
    "testing":       "Testing",
    "bootstrap-product-framing": "Product framing",
    "bootstrap-architecture-baseline": "Architecture baseline",
    "bootstrap-repo-scaffold": "Repo scaffold",
    "bootstrap-work-breakdown": "Work breakdown",
}


def run_setup_job(job: dict) -> None:
    """When a parent task moves to In Progress: create 4 pipeline subtasks,
    then immediately transition the ones with no prerequisites to In Progress.

    Idempotent: if pipeline subtasks already exist, skips creation.
    """
    issue_key = job["issue_key"]
    job_id = job["job_id"]

    try:
        jira_domain = job.get("jira_domain", "")
        notify_pipeline_started(issue_key, job["summary"], jira_domain)

        # Auto-tag parent with domain/service labels
        description_text = job.get("description_text", "")
        auto_labels = suggest_labels(job["summary"], description_text)
        if auto_labels:
            jira.add_labels(issue_key, auto_labels)

        # Check which stages already have subtasks
        existing = jira.get_subtasks(issue_key)
        from dependency_tracker import get_stage
        existing_stages = {
            get_stage(sub["labels"])
            for sub in existing
            if get_stage(sub["labels"])
        }

        created: dict[str, str] = {}  # stage → subtask key

        stages = BOOTSTRAP_STAGES if job.get("summary", "").startswith(BOOTSTRAP_PREFIX) else ALL_STAGES
        for stage in stages:
            if stage in existing_stages:
                logger.info("[%s] subtask for stage %s already exists, skipping", issue_key, stage)
                continue

            label = f"{PIPELINE_LABEL_PREFIX}{stage}"
            summary = f"[{issue_key}] {_STAGE_SUMMARIES.get(stage, stage)}"
            subtask_key = jira.create_subtask(
                parent_key=issue_key,
                summary=summary,
                labels=[label] + (auto_labels or []),
                project_key=JIRA_PROJECT_KEY,
            )
            created[stage] = subtask_key
            logger.info("[%s] created subtask %s for stage %s", issue_key, subtask_key, stage)

        if created:
            notify_subtasks_created(issue_key, list(created.values()),
                                    auto_labels or [], jira_domain)

        jira.add_comment(
            issue_key,
            "🤖 Pipeline ready.\n"
            + "\n".join(
                f"• {_STAGE_SUMMARIES.get(s, s)}: {k}"
                for s, k in created.items()
            )
            + ("\n\nStages with no dependencies start automatically." if created else ""),
        )

        # Trigger stages with no prerequisites → transition to In Progress
        # Jira will fire a webhook for each, which will process them
        all_subtasks = jira.get_subtasks(issue_key)
        for sub in all_subtasks:
            stage = get_stage(sub["labels"])
            if not stage or STAGE_PREREQUISITES.get(stage):
                continue
            sub_status = sub.get("status", "").lower()
            from jira_client import _status_matches

            # Already done or in review → skip
            if (_status_matches(sub["status"], STATUS_DONE)
                    or _status_matches(sub["status"], STATUS_IN_REVIEW)):
                logger.info("[%s] stage %s (%s) already '%s', skipping",
                            issue_key, stage, sub["key"], sub["status"])
                continue

            # "In Progress" but no active job → dead stage, re-launch directly
            if _status_matches(sub["status"], STATUS_IN_PROGRESS):
                from main import jobs
                has_active_job = any(
                    j["issue_key"] == sub["key"] and j["status"] in ("queued", "running")
                    for j in jobs.values()
                )
                if has_active_job:
                    logger.info("[%s] stage %s (%s) has active job, skipping",
                                issue_key, stage, sub["key"])
                    continue
                # Dead stage: already In Progress but no job running — re-launch
                logger.info("[%s] stage %s (%s) is '%s' with no active job, re-launching",
                            issue_key, stage, sub["key"], sub["status"])
                _relaunch_subtask(sub, issue_key, stage)
                continue

            # To Do → transition to In Progress (webhook will trigger job)
            ok = jira.transition(sub["key"], STATUS_IN_PROGRESS)
            if ok:
                logger.info("[%s] auto-started stage %s (%s)", issue_key, stage, sub["key"])
            else:
                available = jira.get_transitions(sub["key"])
                msg = (f"⚠️ Cannot transition {sub['key']} to '{STATUS_IN_PROGRESS}'.\n"
                       f"Available transitions: {available}")
                logger.warning(msg)
                from telegram_notifier import _send
                _send(msg)

        # Also check prerequisite stages: if their deps are all Done,
        # trigger them too (handles restart recovery)
        all_subtasks = jira.get_subtasks(issue_key)
        stage_map = {}
        for sub in all_subtasks:
            s = get_stage(sub["labels"])
            if s:
                stage_map[s] = sub

        for stage, prereqs in STAGE_PREREQUISITES.items():
            if not prereqs:
                continue  # already handled above
            sub = stage_map.get(stage)
            if not sub:
                continue
            # Skip if already done/in review
            if (_status_matches(sub["status"], STATUS_DONE)
                    or _status_matches(sub["status"], STATUS_IN_REVIEW)):
                continue
            # Check if all prerequisites are done
            all_done = all(
                _status_matches(stage_map.get(p, {}).get("status", ""), STATUS_DONE)
                for p in prereqs
            )
            if not all_done:
                continue
            # Prerequisites met — check if needs (re)launch
            if _status_matches(sub["status"], STATUS_IN_PROGRESS):
                from main import jobs as all_jobs
                has_active = any(
                    j["issue_key"] == sub["key"]
                    and j["status"] in ("queued", "running")
                    for j in all_jobs.values()
                )
                if has_active:
                    continue
                logger.info("[%s] re-launching dead stage %s (%s)",
                            issue_key, stage, sub["key"])
                _relaunch_subtask(sub, issue_key, stage)
            else:
                # To Do → start
                logger.info("[%s] prerequisites met for %s, triggering",
                            issue_key, stage)
                ok = jira.transition(sub["key"], STATUS_IN_PROGRESS)
                if ok:
                    logger.info("[%s] auto-started stage %s (%s)",
                                issue_key, stage, sub["key"])

    except Exception as e:
        logger.error("[%s] setup FAIL: %s", issue_key, e)
        try:
            jira.add_comment(issue_key, f"❌ Pipeline setup error: {str(e)[:500]}\nJob: {job_id}")
        except Exception:
            pass


# ── Artifact stage (sys-analysis, architecture) ───────────────────────────────

_ARTIFACT_FILENAMES = {
    "sys-analysis": "SYSTEM_ANALYSIS",
    "architecture": "ARCHITECTURE_DECISION",
    "bootstrap-product-framing": "PRODUCT_BRIEF",
    "bootstrap-architecture-baseline": "ARCHITECTURE_BASELINE",
}


def _artifact_filename(stage: str, parent_key: str) -> str:
    """E.g. docs/decisions/SYSTEM_ANALYSIS_PROJ-38.md"""
    from config import ARTIFACTS_DIR
    # Bootstrap artifacts have fixed, repo-rooted paths.
    if stage == "bootstrap-product-framing":
        return "docs/product/PRODUCT_BRIEF.md"
    if stage == "bootstrap-architecture-baseline":
        # Multi-file stage; return the primary file.
        return "docs/architecture/ARCHITECTURE.md"
    base = _ARTIFACT_FILENAMES.get(stage, stage.upper())
    return f"{ARTIFACTS_DIR}/{base}_{parent_key}.md"


def _extract_first_json_object(text: str) -> dict:
    """Best-effort extraction of a JSON object from model output."""
    out = (text or "").strip()
    start = out.find("{")
    end = out.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object found in output")
    return json.loads(out[start : end + 1])


def _write_files(work_dir: str, files: dict[str, str]) -> list[str]:
    """Write repo-relative files to disk. Returns list of file paths written."""
    written: list[str] = []
    for rel, content in files.items():
        rel = str(rel).lstrip("/").replace("\\", "/")
        fp = os.path.join(work_dir, rel)
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, "w", encoding="utf-8") as fh:
            fh.write(content or "")
        written.append(rel)
    return written


def _extract_unified_diff(text: str) -> str:
    """Extract unified diff starting at first 'diff --git'."""
    if not text:
        return ""
    idx = text.find("diff --git")
    if idx == -1:
        return ""
    return text[idx:].strip() + "\n"


def _apply_unified_diff(work_dir: str, diff_text: str) -> None:
    if not diff_text.strip():
        raise ValueError("empty diff")
    p = subprocess.run(
        ["git", "apply", "--whitespace=nowarn", "-"],
        cwd=work_dir,
        input=diff_text,
        text=True,
        capture_output=True,
    )
    if p.returncode != 0:
        raise Exception(f"git apply failed: {(p.stderr or p.stdout)[:800]}")


def _ensure_description_text(job: dict) -> None:
    """Enrich job with parent summary and description.

    Subtasks only have a pipeline-generated name like "[PROJ-38] Development".
    We need the parent's actual summary and description to give the worker LLM
    enough context.
    """
    from orchestrator import parse_adf_to_text

    # Convert own ADF description
    if not job.get("description_text") and job.get("description"):
        job["description_text"] = parse_adf_to_text(job["description"])

    # For subtasks: fetch parent info (summary + description + epic context)
    if job.get("parent_key") and job["parent_key"] != job.get("issue_key"):
        try:
            parent = jira.get_issue(job["parent_key"])
            parent_fields = parent.get("fields", {})

            # Store parent summary (the actual task name)
            parent_summary = parent_fields.get("summary", "")
            if parent_summary:
                job["parent_summary"] = parent_summary

            # Fetch parent description if we don't have one
            if not job.get("description_text"):
                parent_desc = parent_fields.get("description", {})
                if parent_desc:
                    job["description_text"] = parse_adf_to_text(parent_desc)

            # Try to get epic context too
            epic_ref = parent_fields.get("parent", {})
            if epic_ref and epic_ref.get("key"):
                try:
                    epic = jira.get_issue(epic_ref["key"])
                    epic_fields = epic.get("fields", {})
                    epic_summary = epic_fields.get("summary", "")
                    epic_desc = epic_fields.get("description", {})
                    parts = []
                    if epic_summary:
                        parts.append(f"Epic: {epic_summary}")
                    if epic_desc:
                        parts.append(parse_adf_to_text(epic_desc))
                    if parts:
                        job["epic_context"] = "\n".join(parts)
                except Exception:
                    pass

        except Exception as e:
            logger.warning("Failed to fetch parent info: %s", e)


def run_artifact_stage(job: dict) -> None:
    """Run sys-analysis or architecture via worker LLM (git clone + HTTP API).

    Model returns markdown (stdout); pipeline writes/commits artifact files.
    Former path: **Claude Code** CLI with agentic file edits — stronger for huge repos.
    """
    _ensure_description_text(job)
    issue_key = job["issue_key"]
    parent_key = job["parent_key"]
    stage = job["stage"]
    job_id = job["job_id"]
    work_dir = f"/tmp/pipeline-work/{job_id}"

    try:
        jira.transition(issue_key, STATUS_IN_PROGRESS)
        jira.add_comment(
            issue_key,
            f"🤖 Stage {stage} started (worker LLM {WORKER_LLM_MODEL}). Job: {job_id}",
        )
        notify_stage_started(stage, issue_key, parent_key, job.get("jira_domain", ""))

        auto_labels = suggest_labels(job["summary"], job.get("description_text", ""))
        if auto_labels:
            jira.add_labels(issue_key, auto_labels)
            if parent_key != issue_key:
                jira.add_labels(parent_key, auto_labels)

        artifact_context = collect_artifact_context(parent_key, jira)
        prompt = build_stage_prompt(job, artifact_context)

        repo_cfg = _get_repo_config(job)
        logger.info("[%s] Cloning for artifact stage %s (repo: %s)", issue_key, stage, repo_cfg["repo"])
        os.makedirs(work_dir, exist_ok=True)
        _clone_repo(work_dir, f"analysis/{issue_key.lower()}", repo_cfg)

        start = time.time()
        if job.get("cancelled"):
            raise Exception("Cancelled")
        logger.info("[%s] Worker LLM: running stage %s", issue_key, stage)
        result = _run_claude_with_retry(prompt, work_dir, job)
        duration = int(time.time() - start)

        if result.returncode != 0:
            raise Exception(
                f"Worker LLM rc={result.returncode}: {result.stderr[:500]}"
            )

        written_files: list[str] = []
        artifact_text = ""
        if stage in ("bootstrap-product-framing", "bootstrap-architecture-baseline"):
            data = _extract_first_json_object(result.stdout)
            files = data.get("files") or {}
            if not isinstance(files, dict) or not files:
                raise Exception("Bootstrap artifact stage returned no files")
            written_files = _write_files(work_dir, files)
            artifact_fname = _artifact_filename(stage, parent_key)
            if artifact_fname not in written_files and written_files:
                artifact_fname = written_files[0]
            artifact_path = os.path.join(work_dir, artifact_fname)
            with open(artifact_path, encoding="utf-8", errors="replace") as fh:
                artifact_text = fh.read()
        else:
            artifact_fname = _artifact_filename(stage, parent_key)
            # Ensure artifacts directory exists
            from config import ARTIFACTS_DIR
            os.makedirs(os.path.join(work_dir, ARTIFACTS_DIR), exist_ok=True)
            # Generic filename from prompts vs task-specific path
            generic_fname = _ARTIFACT_FILENAMES.get(stage, stage.upper()) + ".md"
            generic_path = os.path.join(work_dir, generic_fname)
            artifact_path = os.path.join(work_dir, artifact_fname)
            if os.path.exists(generic_path) and generic_fname != artifact_fname:
                os.rename(generic_path, artifact_path)
            if os.path.exists(artifact_path):
                with open(artifact_path, encoding="utf-8") as fh:
                    artifact_text = fh.read()
            else:
                artifact_text = result.stdout.strip() or "Artifact not created — check manually."
                logger.warning("[%s] %s not found, using stdout", issue_key, artifact_fname)
                # Write stdout as artifact so it gets committed
                if artifact_text and artifact_text != "Artifact not created — check manually.":
                    os.makedirs(os.path.dirname(artifact_path), exist_ok=True)
                    with open(artifact_path, "w", encoding="utf-8") as fh:
                        fh.write(artifact_text)

        # Commit and push artifact to feature branch
        branch_name = f"feature/{parent_key.lower()}"
        try:
            subprocess.run(["git", "checkout", "-B", branch_name], cwd=work_dir,
                           check=True, capture_output=True)
            add_paths = written_files or [artifact_fname]
            subprocess.run(["git", "add", *add_paths], cwd=work_dir,
                           check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m",
                 f"{parent_key}: {_STAGE_SUMMARIES.get(stage, stage)} [{stage}]\n\n"
                 "Automated by Claudev"],
                cwd=work_dir, check=True, capture_output=True,
            )
            subprocess.run(
                ["git", "push", "origin", branch_name],
                cwd=work_dir, check=True, capture_output=True, timeout=60,
            )
            logger.info("[%s] pushed artifact %s to %s", issue_key, artifact_fname, branch_name)
        except Exception as e:
            logger.warning("[%s] failed to push artifact: %s", issue_key, e)

        jira_domain = job.get("jira_domain", "")
        parent_url = f"https://{jira_domain}/browse/{parent_key}"
        github_url = f"https://github.com/{repo_cfg['repo']}/blob/{branch_name}/{artifact_fname}"

        jira.add_comment(
            issue_key,
            f"🔗 Task: [{parent_key}]({parent_url})\n\n"
            f"## Stage result: {stage}\n\n{artifact_text[:24000]}\n\n"
            f"---\n⏱ {duration // 60}m {duration % 60}s | Job: {job_id}",
        )
        jira.add_comment(
            parent_key,
            f"✅ Stage **{stage}** complete (worker LLM {WORKER_LLM_MODEL}).\n"
            f"📄 [{artifact_fname}]({github_url})\n"
            f"⏱ {duration // 60}m {duration % 60}s",
        )

        jira.transition(issue_key, STATUS_DONE)
        logger.info("[%s] stage %s done (%ds)", issue_key, stage, duration)
        notify_artifact_done(stage, issue_key, parent_key,
                             job.get("jira_domain", ""), duration)

        triggered = trigger_next_stages(parent_key, stage, jira)
        if triggered:
            jira.add_comment(
                issue_key,
                f"🤖 Automatically triggered stages: {', '.join(triggered)}",
            )

    except Exception as e:
        logger.error("[%s] artifact stage FAIL: %s", issue_key, e)
        notify_error(issue_key, stage, str(e), job.get("jira_domain", ""))
        try:
            jira.add_comment(
                issue_key,
                f"❌ Pipeline error (stage={stage}): {str(e)[:500]}\nJob: {job_id}",
            )
        except Exception:
            pass
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# ── Code stage (development, testing) ─────────────────────────────────────────

def run_code_stage(job: dict) -> None:
    """Run development or testing stage.

    Worker LLM suggests changes in stdout; pipeline still needs real edits in the
    clone for git to see diffs (prompts should ask for patch-style output or file
    blocks). **Claude Code** CLI was better at applying multi-file edits autonomously.
    """
    _ensure_description_text(job)
    issue_key = job["issue_key"]
    parent_key = job["parent_key"]
    stage = job["stage"]
    job_id = job["job_id"]
    work_dir = f"/tmp/pipeline-work/{job_id}"

    try:
        jira.transition(issue_key, STATUS_IN_PROGRESS)
        jira.add_comment(issue_key, f"🤖 Stage {stage} started. Job: {job_id}")
        notify_stage_started(stage, issue_key, parent_key, job.get("jira_domain", ""))

        # Auto-tag issue and parent with domain/service labels
        auto_labels = suggest_labels(job["summary"], job.get("description_text", ""))
        if auto_labels:
            jira.add_labels(issue_key, auto_labels)
            if parent_key != issue_key:
                jira.add_labels(parent_key, auto_labels)

        artifact_context = collect_artifact_context(parent_key, jira)
        prompt = build_stage_prompt(job, artifact_context)

        repo_cfg = _get_repo_config(job)
        branch_name = f"feature/{parent_key.lower()}"
        logger.info("[%s] Cloning for code stage %s (branch %s, repo: %s)",
                    issue_key, stage, branch_name, repo_cfg["repo"])
        os.makedirs(work_dir, exist_ok=True)
        _clone_repo_with_branch(work_dir, branch_name, repo_cfg)

        start = time.time()
        if job.get("cancelled"):
            raise Exception("Cancelled")
        logger.info("[%s] Running worker LLM (stage=%s)", issue_key, stage)
        result = _run_claude_with_retry(prompt, work_dir, job)
        duration = int(time.time() - start)
        logger.info("[%s] Worker LLM: %ds rc=%d", issue_key, duration, result.returncode)

        if result.returncode != 0:
            raise Exception(
                f"Worker LLM rc={result.returncode}: {result.stderr[:500]}"
            )

        # API-only worker cannot edit files directly. Apply unified diff from output.
        diff_text = _extract_unified_diff(result.stdout or "")
        if diff_text:
            _apply_unified_diff(work_dir, diff_text)

        changed = _git_changed_files(work_dir)
        if not changed:
            jira.add_comment(
                issue_key,
                "🤖 Worker LLM made no git changes. "
                "Task may need clarification, stronger model, or restore **Claude Code** CLI.",
            )
            jira.transition(issue_key, "Ready for Dev")
            return

        analysis = analyze_result(result.stdout, changed)

        logger.info("[%s] Pushing %s", issue_key, branch_name)
        subprocess.run(["git", "add", "-A"], cwd=work_dir, check=True)
        subprocess.run(
            [
                "git", "commit", "-m",
                f"{issue_key}: {job['summary']} [{stage}]\n\n"
                "Automated by Claudev",
            ],
            cwd=work_dir, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "push", "origin", branch_name],
            cwd=work_dir, check=True, capture_output=True, timeout=60,
        )

        if stage == "development":
            jira_domain = job.get("jira_domain", os.environ.get("JIRA_DOMAIN", "x"))
            files_list = "\n".join(
                "- " + f for f in analysis.get("files_changed", changed)
            )
            pr_body = (
                f"## {issue_key}: {job['summary']}\n\n"
                f"**Jira:** https://{jira_domain}/browse/{parent_key}\n"
                f"**Subtask:** {issue_key} (stage: {stage})\n"
                "**Automated by:** Claudev\n\n"
                f"### Changes\n{analysis.get('summary_ru', 'N/A')}\n\n"
                f"### Files\n{files_list}\n\n"
                f"### Tests: {analysis.get('tests_status', '?')}\n"
            )
            gh = _github_for_repo(repo_cfg)
            pr = gh.create_pr(
                head=branch_name,
                base=STAGE_BRANCH,
                title=f"{issue_key}: {job['summary']}",
                body=pr_body,
            )
            gh.add_labels(
                pr["number"],
                ["automated", WORKER_LLM_MODEL.replace("/", "-"), stage],
            )
            notify_pr_created(issue_key, parent_key, pr["html_url"],
                              job.get("jira_domain", ""), len(changed))

            concerns = (
                "\n⚠️ " + "; ".join(analysis["concerns"])
                if analysis.get("concerns") else ""
            )
            jira.transition(issue_key, STATUS_DONE)
            jira.add_comment(
                issue_key,
                f"🤖 PR created: {pr['html_url']}\n"
                f"Files: {len(changed)} | "
                f"Tests: {analysis.get('tests_status', '?')} | "
                f"Duration: {duration // 60}m {duration % 60}s\n"
                f"{analysis.get('summary_ru', '')}{concerns}",
            )
            logger.info("[%s] Done! PR #%s", issue_key, pr["number"])

        else:  # testing
            jira.transition(issue_key, STATUS_DONE)
            jira.add_comment(
                issue_key,
                f"🤖 Tests written and pushed to {branch_name}.\n"
                f"Files: {len(changed)} | "
                f"Status: {analysis.get('tests_status', '?')} | "
                f"Duration: {duration // 60}m {duration % 60}s\n"
                f"{analysis.get('summary_ru', '')}",
            )
            notify_testing_done(issue_key, parent_key, job.get("jira_domain", ""), duration)
            logger.info("[%s] Testing stage done (%ds)", issue_key, duration)

        if all_stages_done(parent_key, jira):
            # Auto-transition parent to In Review / Ready for Test
            if AUTO_TRANSITION_ON_COMPLETE:
                jira.transition(parent_key, AUTO_TRANSITION_ON_COMPLETE)
                logger.info("[%s] auto-transitioned → %s", parent_key, AUTO_TRANSITION_ON_COMPLETE)
            jira.add_comment(
                parent_key,
                "🎉 All pipeline stages complete!\n"
                "sys-analysis ✅ | architecture ✅ | development ✅ | testing ✅\n"
                "Task is ready for review.",
            )
            notify_all_done(parent_key, job.get("jira_domain", ""))

        triggered = trigger_next_stages(parent_key, stage, jira)
        if triggered:
            jira.add_comment(
                issue_key,
                f"🤖 Automatically triggered stages: {', '.join(triggered)}",
            )

    except Exception as e:
        logger.error("[%s] code stage FAIL: %s", issue_key, e)
        notify_error(issue_key, stage, str(e), job.get("jira_domain", ""))
        try:
            jira.add_comment(
                issue_key,
                f"❌ Pipeline error (stage={stage}): {str(e)[:500]}\nJob: {job_id}",
            )
        except Exception:
            pass
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# ── Main entry point ──────────────────────────────────────────────────────────

def _create_stage_to_main_pr(gh: GitHubClient, issue_key: str, summary: str) -> None:
    """After merging feature → stage, auto-create PR from stage → main."""
    try:
        # Check if there's already an open stage → main PR
        existing = gh.find_pr(STAGE_BRANCH)
        if existing:
            logger.info("[%s] stage→main PR already exists: #%s",
                        issue_key, existing["number"])
            return

        pr = gh.create_pr(
            head=STAGE_BRANCH,
            base="main",
            title=f"Release: {issue_key} — {summary}",
            body=(
                f"## Auto-release from `{STAGE_BRANCH}` → `main`\n\n"
                f"Triggered by merge of {issue_key}: {summary}\n\n"
                "Created automatically by Claudev."
            ),
        )
        logger.info("[%s] created stage→main PR #%s: %s",
                    issue_key, pr["number"], pr["html_url"])
    except Exception as e:
        logger.warning("[%s] failed to create stage→main PR: %s", issue_key, e)


def run_bootstrap_work_breakdown(job: dict) -> None:
    """BOOTSTRAP Stage 4: create epics/stories/tasks in Jira based on docs."""
    _ensure_description_text(job)
    issue_key = job["issue_key"]
    parent_key = job["parent_key"]
    stage = job.get("stage", STAGE_BOOTSTRAP_WORK_BREAKDOWN)
    job_id = job["job_id"]
    work_dir = f"/tmp/pipeline-work/{job_id}"

    try:
        jira.transition(issue_key, STATUS_IN_PROGRESS)
        jira.add_comment(issue_key, f"🤖 Stage {stage} started. Job: {job_id}")
        notify_stage_started(stage, issue_key, parent_key, job.get("jira_domain", ""))

        repo_cfg = _get_repo_config(job)
        branch_name = f"feature/{parent_key.lower()}"
        os.makedirs(work_dir, exist_ok=True)
        _clone_repo_with_branch(work_dir, branch_name, repo_cfg)

        prompt = build_stage_prompt(job, collect_artifact_context(parent_key, jira))
        start = time.time()
        result = _run_claude_with_retry(prompt, work_dir, job)
        duration = int(time.time() - start)
        if result.returncode != 0:
            raise Exception(f"Worker LLM rc={result.returncode}: {result.stderr[:500]}")

        data = _extract_first_json_object(result.stdout)
        epics = data.get("epics") or []
        if not isinstance(epics, list) or not epics:
            raise Exception("No epics returned for work breakdown")

        # Create epics and tasks in Jira (same approach as planning pipeline)
        created_epics = []
        total_items = 0
        jira_domain = job.get("jira_domain", os.environ.get("JIRA_DOMAIN", ""))

        for epic_data in epics:
            epic_title = epic_data.get("title", "Untitled epic")
            epic_desc = epic_data.get("description", "")
            stories = epic_data.get("stories", []) or []

            epic_body = {
                "fields": {
                    "project": {"key": JIRA_PROJECT_KEY},
                    "summary": epic_title,
                    "description": {
                        "version": 1,
                        "type": "doc",
                        "content": [{
                            "type": "paragraph",
                            "content": [{"type": "text", "text": epic_desc}],
                        }],
                    },
                    "issuetype": {"name": "Epic"},
                }
            }
            r = httpx.post(
                f"{jira.base_url}/rest/api/3/issue",
                headers=jira.headers,
                json=epic_body,
                timeout=15,
            )
            r.raise_for_status()
            epic_key = r.json()["key"]
            created_epics.append({"key": epic_key, "title": epic_title, "stories": []})

            for story_data in stories:
                story_title = story_data.get("title", "Untitled story")
                story_desc = story_data.get("description", "")
                tasks = story_data.get("tasks", []) or []

                story_body = {
                    "fields": {
                        "project": {"key": JIRA_PROJECT_KEY},
                        "parent": {"key": epic_key},
                        "summary": story_title,
                        "description": {
                            "version": 1,
                            "type": "doc",
                            "content": [{
                                "type": "paragraph",
                                "content": [{"type": "text", "text": story_desc}],
                            }],
                        },
                        "issuetype": {"name": "Task"},
                        "labels": (story_data.get("labels", []) or []) + ["bootstrap:story"],
                    }
                }
                r = httpx.post(
                    f"{jira.base_url}/rest/api/3/issue",
                    headers=jira.headers,
                    json=story_body,
                    timeout=15,
                )
                r.raise_for_status()
                story_key = r.json()["key"]
                created_epics[-1]["stories"].append({"key": story_key, "title": story_title, "tasks": []})
                total_items += 1

                for task_data in tasks:
                    task_title = task_data.get("title", "Untitled task")
                    task_desc = task_data.get("description", "")
                    task_labels = task_data.get("labels", []) or []
                    task_body = {
                        "fields": {
                            "project": {"key": JIRA_PROJECT_KEY},
                            "parent": {"key": epic_key},
                            "summary": task_title,
                            "description": {
                                "version": 1,
                                "type": "doc",
                                "content": [{
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": task_desc}],
                                }],
                            },
                            "issuetype": {"name": "Task"},
                            "labels": task_labels,
                        }
                    }
                    r = httpx.post(
                        f"{jira.base_url}/rest/api/3/issue",
                        headers=jira.headers,
                        json=task_body,
                        timeout=15,
                    )
                    r.raise_for_status()
                    tkey = r.json()["key"]
                    created_epics[-1]["stories"][-1]["tasks"].append(tkey)
                    total_items += 1

        # Comment summary
        lines = [
            f"🧩 **BOOTSTRAP breakdown complete** — {len(created_epics)} epics, {total_items} issues created.",
            f"⏱ {duration // 60}m {duration % 60}s | Job: {job_id}",
            "",
        ]
        for e in created_epics:
            lines.append(f"- {e['key']}: {e['title']}")
            for s in e["stories"]:
                lines.append(f"  - {s['key']}: {s['title']}")
                for t in s["tasks"][:10]:
                    lines.append(f"    - {t}")
            lines.append("")
        jira.add_comment(parent_key, "\n".join(lines)[:24000])
        jira.add_comment(issue_key, "\n".join(lines)[:24000])

        jira.transition(issue_key, STATUS_DONE)

        # Optional parent transition after bootstrap
        if AUTO_TRANSITION_ON_BOOTSTRAP_COMPLETE:
            jira.transition(parent_key, AUTO_TRANSITION_ON_BOOTSTRAP_COMPLETE)

        # Trigger normal dev pipeline by creating standard subtasks (idempotent).
        parent_summary = job.get("parent_summary") or job.get("summary", "")
        if parent_summary.startswith(BOOTSTRAP_PREFIX):
            parent_summary = parent_summary[len(BOOTSTRAP_PREFIX):].strip()
        run_setup_job(
            {
                "issue_key": parent_key,
                "job_id": job_id,
                "summary": parent_summary or parent_key,
                "description_text": job.get("description_text", ""),
                "jira_domain": jira_domain,
            }
        )

    except Exception as e:
        logger.error("[%s] bootstrap breakdown FAIL: %s", issue_key, e)
        notify_error(issue_key, stage, str(e), job.get("jira_domain", ""))
        try:
            jira.add_comment(issue_key, f"❌ Bootstrap error: {str(e)[:500]}\nJob: {job_id}")
        except Exception:
            pass
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def run_merge_job(job: dict) -> None:
    """Triggered when parent task moves to STATUS_MERGE ('Ready to Merge').

    Finds the open PR for feature/<issue_key>, merges it into stage,
    then transitions Jira task to Done.
    """
    issue_key = job["issue_key"]
    jira_domain = job.get("jira_domain", "")
    repo_cfg = _get_repo_config(job)
    gh = _github_for_repo(repo_cfg)

    try:
        branch_name = f"feature/{issue_key.lower()}"
        pr = gh.find_pr(branch_name)

        if not pr:
            jira.add_comment(
                issue_key,
                f"⚠️ Pipeline: no open PR found for branch `{branch_name}`. "
                "Please merge manually.",
            )
            return

        pr_number = pr["number"]
        pr_url = pr["html_url"]
        logger.info("[%s] Merging PR #%s into %s", issue_key, pr_number, pr["base"]["ref"])

        merge_result = gh.merge_pr(
            pr_number,
            commit_message=f"{issue_key}: {job['summary']} (auto-merge)",
        )

        if not merge_result.get("merged"):
            raise Exception(f"GitHub merge failed: {merge_result.get('message')}")

        jira.transition(issue_key, STATUS_DONE)
        jira.add_comment(
            issue_key,
            f"🎉 Merged into `{pr['base']['ref']}`!\n"
            f"PR: {pr_url}\n"
            f"Commit: {merge_result.get('sha', '')[:8]}",
        )
        notify_merged(issue_key, pr_url, pr["base"]["ref"], jira_domain)
        logger.info("[%s] merged PR #%s → Done", issue_key, pr_number)

        # Auto-create PR from stage → main
        base_branch = pr["base"]["ref"]  # usually "stage"
        if base_branch == STAGE_BRANCH:
            _create_stage_to_main_pr(gh, issue_key, job["summary"])

    except Exception as e:
        logger.error("[%s] merge FAIL: %s", issue_key, e)
        notify_error(issue_key, "merge", str(e), jira_domain)
        try:
            jira.add_comment(
                issue_key,
                f"❌ Auto-merge failed: {str(e)[:400]}\n"
                "Please merge the PR manually and move the task to Done.",
            )
        except Exception:
            pass


def run_job(job: dict) -> None:
    """Route job to the correct handler.

    PLAN: task (no stage)   → run_plan_job: break feature into epics/tasks
    Parent task (no stage)  → run_setup_job: create subtasks, start first stages
    Sub-task artifact stage → run_artifact_stage: worker LLM → markdown
    Sub-task code stage     → run_code_stage: worker LLM + git/PR
    """
    from config import PLAN_PREFIX
    stage = job.get("stage")

    if job.get("trigger") == STATUS_MERGE:
        run_merge_job(job)
    elif stage is None and job.get("summary", "").startswith(PLAN_PREFIX):
        run_plan_job(job)
    elif stage is None and job.get("summary", "").startswith(BOOTSTRAP_PREFIX):
        run_setup_job(job)
    elif stage is None:
        run_setup_job(job)
    elif stage in ARTIFACT_STAGES:
        run_artifact_stage(job)
    elif stage in CODE_STAGES:
        run_code_stage(job)
    elif stage == STAGE_BOOTSTRAP_WORK_BREAKDOWN:
        run_bootstrap_work_breakdown(job)
    else:
        logger.warning("[%s] Unknown stage '%s', falling back to legacy", job["issue_key"], stage)
        _run_legacy_job(job)


# ── Legacy single-stage flow (backward compatibility) ─────────────────────────

def _run_legacy_job(job: dict) -> None:
    """Original single-stage worker for tasks without pipeline labels."""
    from orchestrator import (
        parse_adf_to_text,
        classify_issue,
        build_claude_prompt,
        analyze_result as _analyze,
    )

    issue_key = job["issue_key"]
    job_id = job["job_id"]
    work_dir = f"/tmp/pipeline-work/{job_id}"

    try:
        jira.transition(issue_key, "In Progress")
        jira.add_comment(issue_key, f"🤖 Pipeline started. Job: {job_id}")

        description_text = parse_adf_to_text(job.get("description", ""))
        issue = {
            "key": issue_key,
            "summary": job["summary"],
            "description_text": description_text,
            "issue_type": job.get("issue_type", "Task"),
            "priority": job.get("priority", "Medium"),
            "labels": job.get("labels", []),
            "components": job.get("components", []),
        }

        classification = classify_issue(
            issue["summary"], description_text, issue["labels"]
        )
        prompt = build_claude_prompt(issue, classification)

        branch_name = f"feature/{issue_key.lower()}"
        os.makedirs(work_dir, exist_ok=True)
        _clone_repo(work_dir, branch_name)

        start = time.time()
        result = _run_claude_with_retry(prompt, work_dir, job)
        duration = int(time.time() - start)
        logger.info("[%s] Worker LLM: %ds rc=%d", issue_key, duration, result.returncode)

        changed = _git_changed_files(work_dir)
        if not changed:
            jira.add_comment(
                issue_key,
                "🤖 Worker LLM made no git changes. Task needs clarification or stronger model.",
            )
            jira.transition(issue_key, "Ready for Dev")
            return

        analysis = _analyze(result.stdout, changed)

        subprocess.run(["git", "add", "-A"], cwd=work_dir, check=True)
        subprocess.run(
            [
                "git", "commit", "-m",
                f"{issue_key}: {issue['summary']}\n\nAutomated by Claudev",
            ],
            cwd=work_dir, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "push", "origin", branch_name],
            cwd=work_dir, check=True, capture_output=True, timeout=60,
        )

        jira_domain = job.get("jira_domain", os.environ.get("JIRA_DOMAIN", "x"))
        files_list = "\n".join(
            "- " + f for f in analysis.get("files_changed", changed)
        )
        pr_body = (
            f"## {issue_key}: {issue['summary']}\n\n"
            f"**Jira:** https://{jira_domain}/browse/{issue_key}\n"
            "**Automated by:** Claudev\n\n"
            f"### Changes\n{analysis.get('summary_ru', 'N/A')}\n\n"
            f"### Files\n{files_list}\n\n"
            f"### Tests: {analysis.get('tests_status', '?')}\n"
        )
        pr = github.create_pr(
            head=branch_name,
            base=STAGE_BRANCH,
            title=f"{issue_key}: {issue['summary']}",
            body=pr_body,
        )
        github.add_labels(
            pr["number"],
            ["automated", WORKER_LLM_MODEL.replace("/", "-")],
        )

        concerns = (
            "\n⚠️ " + "; ".join(analysis["concerns"])
            if analysis.get("concerns") else ""
        )
        jira.transition(issue_key, "In Review")
        jira.add_comment(
            issue_key,
            f"🤖 PR created: {pr['html_url']}\n"
            f"Files: {len(changed)} | "
            f"Tests: {analysis.get('tests_status', '?')} | "
            f"Duration: {duration // 60}m {duration % 60}s\n"
            f"{analysis.get('summary_ru', '')}{concerns}",
        )
        logger.info("[%s] Done! PR #%s", issue_key, pr["number"])

    except Exception as e:
        logger.error("[%s] FAIL: %s", issue_key, e)
        try:
            jira.add_comment(
                issue_key,
                f"❌ Pipeline error: {str(e)[:500]}\nJob: {job_id}",
            )
        except Exception:
            pass
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
