"""Telegram notifications and bot commands for the dev pipeline."""
import logging
import os

import httpx

logger = logging.getLogger("pipeline.telegram")

_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
_BASE = "https://api.telegram.org"


def _send(text: str, chat_id: str = "") -> None:
    if not _TOKEN:
        return
    target = chat_id or _CHAT_ID
    if not target:
        return
    try:
        httpx.post(
            f"{_BASE}/bot{_TOKEN}/sendMessage",
            json={"chat_id": target, "text": text, "parse_mode": "HTML"},
            timeout=5,
        )
    except Exception as e:
        logger.warning("Telegram send failed: %s", e)


def _reply(chat_id: str, text: str) -> None:
    """Reply to a specific chat (used by bot commands)."""
    _send(text, chat_id=str(chat_id))


# ── Bot command handler ───────────────────────────────────────────────────────

_HELP_TEXT = (
    "🤖 <b>Claudev Bot</b>\n\n"
    "<b>Commands:</b>\n"
    "/new <code>Summary text</code> — create a task and start the dev pipeline\n"
    "/plan <code>Feature description</code> — create a PLAN: task "
    "(AI breaks it into epics and tasks)\n"
    "/start <code>PROJ-123</code> — move task to In Progress (starts pipeline)\n"
    "/cancel <code>PROJ-123</code> — cancel a running task\n"
    "/status — show active pipelines and queue\n"
    "/status <code>PROJ-123</code> — show task status in Jira\n"
    "/help — this message\n\n"
    "<b>Quick task:</b> <code>/new Fix login timeout</code> — starts coding\n"
    "<b>Feature planning:</b> <code>/plan User auth with OAuth2</code> "
    "— creates epics and tasks"
)


def handle_telegram_update(update: dict) -> dict:
    """Process an incoming Telegram update (webhook).

    Returns a dict with the action taken.
    """
    message = update.get("message", {})
    text = message.get("text", "").strip()
    chat_id = message.get("chat", {}).get("id", "")

    if not text or not chat_id:
        return {"ok": True, "action": "ignored"}

    # Only process commands
    if not text.startswith("/"):
        return {"ok": True, "action": "not_a_command"}

    parts = text.split(maxsplit=1)
    command = parts[0].lower().split("@")[0]  # strip @botname
    arg = parts[1].strip() if len(parts) > 1 else ""

    try:
        if command == "/help":
            _reply(chat_id, _HELP_TEXT)
            return {"ok": True, "action": "help"}

        elif command == "/new":
            return _cmd_new_task(chat_id, arg)

        elif command == "/plan":
            return _cmd_plan_task(chat_id, arg)

        elif command == "/start":
            return _cmd_start_task(chat_id, arg)

        elif command == "/cancel":
            return _cmd_cancel_task(chat_id, arg)

        elif command == "/status":
            return _cmd_status(chat_id, arg)

        else:
            _reply(chat_id, f"Unknown command: {command}\nSend /help for available commands.")
            return {"ok": True, "action": "unknown_command"}

    except Exception as e:
        logger.error("Telegram command error: %s", e)
        _reply(chat_id, f"❌ Error: {str(e)[:200]}")
        return {"ok": False, "error": str(e)[:200]}


def _cmd_new_task(chat_id: str, summary: str) -> dict:
    """Create a new Jira task and immediately start the pipeline."""
    if not summary:
        _reply(chat_id, "Usage: /new <task summary>\nExample: /new Fix login timeout on mobile")
        return {"ok": True, "action": "new_task_no_args"}

    from jira_client import JiraClient
    from config import JIRA_PROJECT_KEY, JIRA_DOMAIN, STATUS_IN_PROGRESS

    jira = JiraClient()

    # Create the task
    body = {
        "fields": {
            "project": {"key": JIRA_PROJECT_KEY},
            "summary": summary,
            "issuetype": {"name": "Task"},
        }
    }
    import httpx as _httpx
    r = _httpx.post(
        f"{jira.base_url}/rest/api/3/issue",
        headers=jira.headers,
        json=body,
        timeout=10,
    )
    r.raise_for_status()
    issue_key = r.json()["key"]
    jira_url = f"https://{JIRA_DOMAIN}.atlassian.net/browse/{issue_key}"

    # Transition to In Progress to trigger the pipeline
    jira.transition(issue_key, STATUS_IN_PROGRESS)

    _reply(
        chat_id,
        f"✅ <b>Task created and started!</b>\n"
        f"<a href='{jira_url}'>{issue_key}</a>: {summary}\n"
        f"Pipeline will pick it up automatically."
    )
    return {"ok": True, "action": "new_task", "issue_key": issue_key}


def _cmd_plan_task(chat_id: str, description: str) -> dict:
    """Create a PLAN: task — Claude Code will break it into epics and tasks."""
    if not description:
        _reply(chat_id,
               "Usage: /plan <feature description>\n"
               "Example: /plan User authentication with OAuth2 and social login")
        return {"ok": True, "action": "plan_no_args"}

    from jira_client import JiraClient
    from config import JIRA_PROJECT_KEY, JIRA_DOMAIN, STATUS_IN_PROGRESS, PLAN_PREFIX

    jira = JiraClient()
    summary = f"{PLAN_PREFIX} {description}"

    body = {
        "fields": {
            "project": {"key": JIRA_PROJECT_KEY},
            "summary": summary,
            "issuetype": {"name": "Task"},
        }
    }
    import httpx as _httpx
    r = _httpx.post(
        f"{jira.base_url}/rest/api/3/issue",
        headers=jira.headers,
        json=body,
        timeout=10,
    )
    r.raise_for_status()
    issue_key = r.json()["key"]
    jira_url = f"https://{JIRA_DOMAIN}.atlassian.net/browse/{issue_key}"

    jira.transition(issue_key, STATUS_IN_PROGRESS)

    _reply(
        chat_id,
        f"📝 <b>Planning task created!</b>\n"
        f"<a href='{jira_url}'>{issue_key}</a>: {summary}\n"
        f"Claude Code will analyze the codebase and create epics + tasks."
    )
    return {"ok": True, "action": "plan_task", "issue_key": issue_key}


def _cmd_start_task(chat_id: str, issue_key: str) -> dict:
    """Move an existing task to In Progress."""
    if not issue_key:
        _reply(chat_id, "Usage: /start PROJ-123")
        return {"ok": True, "action": "start_no_args"}

    issue_key = issue_key.upper()
    from jira_client import JiraClient
    from config import JIRA_DOMAIN, STATUS_IN_PROGRESS

    jira = JiraClient()
    ok = jira.transition(issue_key, STATUS_IN_PROGRESS)
    jira_url = f"https://{JIRA_DOMAIN}.atlassian.net/browse/{issue_key}"

    if ok:
        _reply(chat_id, f"▶️ <a href='{jira_url}'>{issue_key}</a> → In Progress\nPipeline triggered.")
    else:
        available = jira.get_transitions(issue_key)
        _reply(chat_id, f"⚠️ Cannot move {issue_key} to In Progress.\nAvailable: {', '.join(available)}")

    return {"ok": True, "action": "start_task", "issue_key": issue_key, "transitioned": ok}


def _cmd_cancel_task(chat_id: str, issue_key: str) -> dict:
    """Move a task to Cancelled."""
    if not issue_key:
        _reply(chat_id, "Usage: /cancel PROJ-123")
        return {"ok": True, "action": "cancel_no_args"}

    issue_key = issue_key.upper()
    from jira_client import JiraClient
    from config import JIRA_DOMAIN, STATUS_CANCELLED

    jira = JiraClient()
    ok = jira.transition(issue_key, STATUS_CANCELLED)
    jira_url = f"https://{JIRA_DOMAIN}.atlassian.net/browse/{issue_key}"

    if ok:
        _reply(chat_id, f"🛑 <a href='{jira_url}'>{issue_key}</a> → Cancelled")
    else:
        _reply(chat_id, f"⚠️ Cannot cancel {issue_key}. Check Jira workflow.")

    return {"ok": True, "action": "cancel_task", "issue_key": issue_key}


def _cmd_status(chat_id: str, issue_key: str) -> dict:
    """Show pipeline status or specific task status."""
    if issue_key:
        # Specific task
        issue_key = issue_key.upper()
        from jira_client import JiraClient
        from config import JIRA_DOMAIN

        jira = JiraClient()
        try:
            issue = jira.get_issue(issue_key)
            fields = issue.get("fields", {})
            status = fields.get("status", {}).get("name", "?")
            summary = fields.get("summary", "")
            jira_url = f"https://{JIRA_DOMAIN}.atlassian.net/browse/{issue_key}"

            # Check subtasks
            subtasks = jira.get_subtasks(issue_key)
            if subtasks:
                stages = []
                for sub in subtasks:
                    stage_label = next((l for l in sub.get("labels", []) if l.startswith("pipeline:")), "")
                    stage_name = stage_label.replace("pipeline:", "") if stage_label else "?"
                    emoji = {"Done": "✅", "In Progress": "⚙️", "To Do": "⏳"}.get(sub["status"], "❓")
                    stages.append(f"  {emoji} {stage_name}: {sub['status']}")
                stages_text = "\n".join(stages)
            else:
                stages_text = "  No pipeline subtasks"

            _reply(
                chat_id,
                f"📋 <a href='{jira_url}'>{issue_key}</a>: {summary}\n"
                f"Status: <b>{status}</b>\n\n"
                f"Stages:\n{stages_text}"
            )
        except Exception as e:
            _reply(chat_id, f"❌ Cannot fetch {issue_key}: {str(e)[:200]}")

        return {"ok": True, "action": "status_task", "issue_key": issue_key}

    else:
        # General pipeline status
        from main import active_pipelines, pipeline_queue, jobs, active_count

        active_list = ", ".join(active_pipelines) if active_pipelines else "none"
        queued_list = ", ".join(q["issue_key"] for q in pipeline_queue) if pipeline_queue else "none"

        running_jobs = [
            f"  • {j['issue_key']} ({j.get('stage', 'setup')})"
            for j in jobs.values()
            if j["status"] == "running"
        ]
        running_text = "\n".join(running_jobs) if running_jobs else "  none"

        _reply(
            chat_id,
            f"📊 <b>Pipeline Status</b>\n\n"
            f"Active pipelines: {active_list}\n"
            f"Queued: {queued_list}\n"
            f"Running jobs ({active_count}):\n{running_text}"
        )
        return {"ok": True, "action": "status_general"}


# ── Notification functions (existing) ─────────────────────────────────────────

def notify_pipeline_started(parent_key: str, summary: str, jira_domain: str) -> None:
    url = f"https://{jira_domain}/browse/{parent_key}"
    _send(
        f"🚦 <b>Pipeline started</b>\n"
        f"Task: <a href='{url}'>{parent_key}</a>\n"
        f"{summary}"
    )


def notify_subtasks_created(parent_key: str, subtask_keys: list[str],
                             labels: list[str], jira_domain: str) -> None:
    url = f"https://{jira_domain}/browse/{parent_key}"
    keys_str = " · ".join(subtask_keys)
    labels_str = " ".join(f"#{l}" for l in labels) if labels else "—"
    _send(
        f"📋 <b>Subtasks created</b>\n"
        f"Task: <a href='{url}'>{parent_key}</a>\n"
        f"Subtasks: {keys_str}\n"
        f"Labels: {labels_str}"
    )


def notify_stage_started(stage: str, issue_key: str, parent_key: str,
                          jira_domain: str) -> None:
    emoji = {"sys-analysis": "📊", "architecture": "🏗",
             "development": "💻", "testing": "🧪"}.get(stage, "⚙️")
    name = {"sys-analysis": "System Analysis", "architecture": "Architecture",
            "development": "Development", "testing": "Testing"}.get(stage, stage)
    url = f"https://{jira_domain}/browse/{issue_key}"
    _send(
        f"{emoji} <b>{name} — started</b>\n"
        f"Task: <a href='https://{jira_domain}/browse/{parent_key}'>{parent_key}</a>\n"
        f"Subtask: <a href='{url}'>{issue_key}</a>\n"
        f"Claude Code is working..."
    )


def notify_artifact_done(stage: str, issue_key: str, parent_key: str,
                         jira_domain: str, duration_s: int) -> None:
    emoji = "📊" if stage == "sys-analysis" else "🏗"
    title = "System Analysis" if stage == "sys-analysis" else "Architecture Decision"
    url = f"https://{jira_domain}/browse/{issue_key}"
    _send(
        f"{emoji} <b>{title} ready</b>\n"
        f"Task: <a href='https://{jira_domain}/browse/{parent_key}'>{parent_key}</a>\n"
        f"Subtask: <a href='{url}'>{issue_key}</a>\n"
        f"⏱ {duration_s // 60}m {duration_s % 60}s"
    )


def notify_pr_created(issue_key: str, parent_key: str, pr_url: str,
                      jira_domain: str, files_count: int) -> None:
    _send(
        f"🔀 <b>PR created</b>\n"
        f"Task: <a href='https://{jira_domain}/browse/{parent_key}'>{parent_key}</a>\n"
        f"<a href='{pr_url}'>Open PR</a> · {files_count} files"
    )


def notify_testing_done(issue_key: str, parent_key: str,
                        jira_domain: str, duration_s: int) -> None:
    url = f"https://{jira_domain}/browse/{issue_key}"
    _send(
        f"🧪 <b>Tests written</b>\n"
        f"Task: <a href='https://{jira_domain}/browse/{parent_key}'>{parent_key}</a>\n"
        f"Subtask: <a href='{url}'>{issue_key}</a>\n"
        f"⏱ {duration_s // 60}m {duration_s % 60}s"
    )


def notify_all_done(parent_key: str, jira_domain: str) -> None:
    _send(
        f"✅ <b>Ready for review!</b>\n"
        f"<a href='https://{jira_domain}/browse/{parent_key}'>{parent_key}</a>\n"
        f"All stages complete: analysis → architecture → code → tests\n"
        f"Review the PR and move the task to <b>Ready to Merge</b>"
    )


def notify_merged(issue_key: str, pr_url: str, base_branch: str,
                  jira_domain: str) -> None:
    url = f"https://{jira_domain}/browse/{issue_key}"
    _send(
        f"🚀 <b>Merged into {base_branch}!</b>\n"
        f"Task: <a href='{url}'>{issue_key}</a>\n"
        f"<a href='{pr_url}'>PR</a> → Done"
    )


def notify_error(issue_key: str, stage: str, error: str, jira_domain: str) -> None:
    url = f"https://{jira_domain}/browse/{issue_key}"
    _send(
        f"❌ <b>Pipeline error</b>\n"
        f"Stage: {stage}\n"
        f"Task: <a href='{url}'>{issue_key}</a>\n"
        f"<code>{error[:200]}</code>"
    )
