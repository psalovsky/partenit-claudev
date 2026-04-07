"""
Stage-specific prompts for the pipeline worker LLM (default **deepseek-chat**).

Each function returns plain text sent to the worker HTTP API (with repo snapshot).
Historically these strings were passed to **Claude Code** CLI — stronger models
recommended for large refactors (see commented CLI block in ``worker.py``).

Stages:
  sys-analysis  → produce SYSTEM_ANALYSIS.md artifact
  architecture  → produce ARCHITECTURE_DECISION.md artifact
  development   → write code, open PR to stage branch
  testing       → write tests, push to dev branch
"""
from __future__ import annotations


# ── Shared header ─────────────────────────────────────────────────────────────

def _base_header(issue: dict) -> str:
    parent_summary = issue.get('parent_summary', issue['summary'])
    epic_section = ""
    if issue.get('epic_context'):
        epic_section = (
            "## Epic context\n"
            f"{issue['epic_context']}\n\n"
        )

    desc_text = issue.get('description_text', '')
    if desc_text:
        desc_section = (
            "## Parent task description\n"
            f"{desc_text}\n\n"
        )
    else:
        desc_section = (
            "## Parent task description\n"
            "(No description provided. Use the task title "
            "and epic context above as guidance.)\n\n"
        )

    return (
        f"## Task: {issue['parent_key']} — {parent_summary}\n\n"
        f"Subtask: {issue['key']} | "
        f"Stage: **{issue['stage']}** | "
        f"Priority: {issue.get('priority', 'Medium')}\n"
        f"Components: {', '.join(issue.get('components', []) or [])}\n\n"
        + epic_section
        + desc_section
    )


# ── Mandatory reading + coding standards ──────────────────────────────────────

def _pre_flight(stage: str, parent_key: str = "") -> str:
    """Files to read BEFORE starting any work."""
    base_files = (
        "## Mandatory reading before starting\n\n"
        "Read these files IN FULL before writing anything "
        "(if they exist in the repo):\n\n"
        "1. **CLAUDE.md** (or `docs/governance/CLAUDE.md`) — project rules, conventions, "
        "priorities for AI assistants\n"
        "2. **ARCHITECTURE.md** (or `docs/architecture/ARCHITECTURE.md`) — project structure, components, "
        "dependencies, data flows\n"
        "3. **STEERING.md** (or `docs/governance/STEERING.md`) — design principles, constraints, "
        "things that must not be changed\n"
    )

    if stage in ("architecture", "development", "testing"):
        sa_file = f"docs/decisions/SYSTEM_ANALYSIS_{parent_key}.md" if parent_key else "docs/decisions/SYSTEM_ANALYSIS*.md"
        base_files += (
            f"4. **{sa_file}** — system analysis for this task "
            "(if present in the repo)\n"
        )

    if stage in ("development", "testing"):
        ad_file = (f"docs/decisions/ARCHITECTURE_DECISION_{parent_key}.md"
                   if parent_key else "docs/decisions/ARCHITECTURE_DECISION*.md")
        base_files += (
            f"5. **{ad_file}** — architecture decision for this task "
            "(if present in the repo)\n"
        )

    return base_files + "\n"


# ── BOOTSTRAP stages (greenfield) ─────────────────────────────────────────────

def build_bootstrap_product_framing_prompt(issue: dict) -> str:
    return (
        _base_header(issue)
        + _pre_flight(issue.get("stage", ""))
        + "## BOOTSTRAP Stage 1: Product framing\n\n"
        "Analyze the idea and generate `docs/product/PRODUCT_BRIEF.md`.\n\n"
        "Include:\n"
        "- product vision\n"
        "- target users\n"
        "- use cases\n"
        "- MVP scope\n"
        "- non-goals\n"
        "- constraints (tech, infra, deployment)\n"
        "- success metrics\n\n"
        "Output format:\n"
        "Return ONLY JSON (no backticks, no prose) of the form:\n"
        "{\n"
        "  \"files\": {\n"
        "    \"docs/product/PRODUCT_BRIEF.md\": \"<markdown>\"\n"
        "  }\n"
        "}\n"
    ).strip()


def build_bootstrap_architecture_baseline_prompt(issue: dict) -> str:
    return (
        _base_header(issue)
        + _pre_flight(issue.get("stage", ""))
        + "## BOOTSTRAP Stage 2: Architecture baseline\n\n"
        "Based on PRODUCT_BRIEF, design a baseline architecture and governance rules.\n\n"
        "Generate these files:\n"
        "- `docs/architecture/ARCHITECTURE.md`\n"
        "- `docs/governance/STEERING.md`\n"
        "- `docs/governance/CLAUDE.md`\n\n"
        "STEERING.md MUST include strict rules (forbidden patterns, constraints).\n"
        "CLAUDE.md MUST contain AI coding rules (allowed changes, style, test rules).\n\n"
        "Output format:\n"
        "Return ONLY JSON (no backticks, no prose):\n"
        "{\n"
        "  \"files\": {\n"
        "    \"docs/architecture/ARCHITECTURE.md\": \"<markdown>\",\n"
        "    \"docs/governance/STEERING.md\": \"<markdown>\",\n"
        "    \"docs/governance/CLAUDE.md\": \"<markdown>\"\n"
        "  }\n"
        "}\n"
    ).strip()


def build_bootstrap_repo_scaffold_prompt(issue: dict) -> str:
    return (
        _base_header(issue)
        + _pre_flight(issue.get("stage", ""))
        + _coding_standards()
        + "## BOOTSTRAP Stage 3: Repository scaffold\n\n"
        "Create an initial repository structure for a greenfield project.\n\n"
        "Requirements:\n"
        "- FastAPI app skeleton\n"
        "- Docker setup (Dockerfile + docker-compose)\n"
        "- Basic tests (pytest)\n"
        "- Config files\n"
        "- CI pipeline (GitHub Actions) if reasonable\n"
        "- Folder structure:\n"
        "  - `src/api`, `src/core`, `src/services`, `src/models`\n"
        "  - `tests/`\n"
        "  - `docs/`\n"
        "  - `docker/`\n"
        "- Add/Update `README.md` with how to run.\n\n"
        "IMPORTANT: You MUST output a single unified diff that can be applied with `git apply`.\n"
        "Output ONLY the diff, starting with `diff --git` lines.\n"
    ).strip()


def build_bootstrap_work_breakdown_prompt(issue: dict) -> str:
    return (
        "## BOOTSTRAP Stage 4: Work breakdown\n\n"
        f"Project: **{issue.get('parent_summary', issue.get('summary',''))}**\n\n"
        + (f"## Description\n\n{issue.get('description_text','')}\n\n" if issue.get("description_text") else "")
        + "Use PRODUCT_BRIEF and ARCHITECTURE baseline (from repo snapshot) to propose:\n"
        "- epics\n"
        "- stories\n"
        "- tasks\n\n"
        "Each task must be small, testable, and mapped to architecture components.\n\n"
        "Output format:\n"
        "Return ONLY JSON (no backticks, no prose) of the form:\n"
        "{\n"
        "  \"epics\": [\n"
        "    {\n"
        "      \"title\": \"...\",\n"
        "      \"description\": \"...\",\n"
        "      \"stories\": [\n"
        "        {\n"
        "          \"title\": \"...\",\n"
        "          \"description\": \"...\",\n"
        "          \"tasks\": [\n"
        "            {\"title\":\"...\",\"description\":\"...\",\"labels\":[\"...\"]}\n"
        "          ]\n"
        "        }\n"
        "      ]\n"
        "    }\n"
        "  ]\n"
        "}\n"
    ).strip()


def _coding_standards() -> str:
    """Coding standards that apply to ALL stages.

    NOTE: Customize these to match your project's conventions.
    The rules below are sensible defaults — adjust as needed.
    """
    return (
        "## Coding standards\n\n"
        "### Architecture\n"
        "- **No code duplication.** Before creating a new function/class, "
        "search the codebase for existing implementations. "
        "Use `Grep` to search.\n"
        "- **No unnecessary abstractions.** "
        "Three similar lines are better than a premature abstraction.\n"
        "- **Follow existing patterns.** Look at how similar features "
        "are implemented in the project and stay consistent.\n"
        "- **Keep files focused.** One module = one responsibility. "
        "Split large files by concern.\n\n"
        "### Style\n"
        "- Follow the coding style already used in the project.\n"
        "- Logging: use the project's logging pattern, NOT print().\n"
        "- Type hints for public functions.\n"
        "- Docstrings only for non-obvious logic.\n\n"
        "### What NOT to do\n"
        "- Do NOT refactor code unrelated to the task.\n"
        "- Do NOT add comments/docstrings to code you didn't change.\n"
        "- Do NOT add error handling for impossible scenarios.\n"
        "- Do NOT create utility helpers for one-off operations.\n"
        "- Do NOT add feature flags or backwards-compatibility shims.\n"
        "- Do NOT create git commits — the pipeline handles that.\n\n"
    )


def _post_flight() -> str:
    """Checklist AFTER completing work."""
    return (
        "## After completing work\n\n"
        "1. Do NOT modify ARCHITECTURE.md, README.md, or other shared "
        "docs — this causes merge conflicts in parallel pipelines.\n"
        "2. If you noticed tech debt — add to TECH_DEBT.md.\n"
        "3. If unclear → leave a TODO with explanation, "
        "don't guess.\n\n"
    )


def _test_loop() -> str:
    """Instructions to iterate until tests pass."""
    return (
        "## Test loop (MANDATORY)\n\n"
        "After finishing code/tests, run this loop:\n\n"
        "```\n"
        "repeat:\n"
        "  1. pytest tests/unit/ -x -v\n"
        "  2. if all green → STOP, work is done\n"
        "  3. if FAIL/ERROR → read the traceback\n"
        "  4. determine the cause: bug in code or wrong expectation in test\n"
        "  5. fix the ROOT CAUSE — if it's a code bug, fix the code, "
        "don't adjust the test to match the bug\n"
        "  6. goto 1\n"
        "```\n\n"
        "Maximum 5 iterations. If tests still fail after 5 — "
        "leave a TODO describing the issue.\n\n"
        "IMPORTANT: do not delete failing tests! Fix the code or "
        "correct the test if the expectation is wrong.\n\n"
    )


# ── Stage: sys-analysis ────────────────────────────────────────────────────────

def build_sys_analysis_prompt(issue: dict) -> str:
    jira_domain = issue.get("jira_domain", "")
    parent_key = issue.get("parent_key", issue["key"])
    parent_summary = issue.get("parent_summary", issue["summary"])
    parent_url = (f"https://{jira_domain}/browse/{parent_key}"
                  if jira_domain else parent_key)
    subtask_url = (f"https://{jira_domain}/browse/{issue['key']}"
                   if jira_domain else issue['key'])

    file_header = (
        f"# System Analysis: [{parent_key}]({parent_url})"
        f" — {parent_summary}\n\n"
        f"> **Jira:** [{parent_key}]({parent_url}) · "
        f"Subtask: [{issue['key']}]({subtask_url})  \n"
        f"> **Stage:** sys-analysis  \n"
        "> Auto-generated by Claudev\n\n"
        "---\n\n"
    )

    return (
        _base_header(issue)
        + _pre_flight("sys-analysis", parent_key)
        + "## What to do: System Analysis\n\n"
        "Perform a system analysis of the task. Read the code of affected "
        "components, understand the current state, and create the file "
        f"`docs/decisions/SYSTEM_ANALYSIS_{parent_key}.md`.\n\n"
        f"The file MUST start with exactly this header "
        f"(copy verbatim):\n\n"
        f"```\n{file_header}```\n\n"
        "Then add these sections:\n"
        "1. **Problem summary** — what exactly is required\n"
        "2. **Current state of the code** — how it works now "
        "(read real code, don't guess!)\n"
        "3. **Affected components** — list of modules/packages "
        "with file paths\n"
        "4. **Dependencies** — upstream/downstream, who calls whom\n"
        "5. **Existing utilities** — what's already in the codebase that can "
        "be reused (check with Grep!)\n"
        "6. **Risks** — potential issues during implementation\n"
        "7. **Edge cases** — non-standard situations\n"
        "8. **Recommended approach** — concrete implementation steps "
        "with file paths\n\n"
        "Format: markdown, lists, code examples where needed.\n"
        "Length: 200-500 lines — detailed but to the point.\n\n"
    ).strip()


# ── Stage: architecture ────────────────────────────────────────────────────────

def build_architecture_prompt(issue: dict, sys_analysis: str = "") -> str:
    jira_domain = issue.get("jira_domain", "")
    parent_key = issue.get("parent_key", issue["key"])
    parent_summary = issue.get("parent_summary", issue["summary"])
    parent_url = (f"https://{jira_domain}/browse/{parent_key}"
                  if jira_domain else parent_key)
    subtask_url = (f"https://{jira_domain}/browse/{issue['key']}"
                   if jira_domain else issue['key'])

    file_header = (
        f"# Architecture Decision: [{parent_key}]({parent_url})"
        f" — {parent_summary}\n\n"
        f"> **Jira:** [{parent_key}]({parent_url}) · "
        f"Subtask: [{issue['key']}]({subtask_url})  \n"
        f"> **Stage:** architecture  \n"
        "> Auto-generated by Claudev\n\n"
        "---\n\n"
    )

    context_section = ""
    if sys_analysis:
        context_section = (
            "## System analysis result (previous stage)\n\n"
            f"{sys_analysis[:4000]}\n\n"
        )

    return (
        _base_header(issue)
        + _pre_flight("architecture", parent_key)
        + context_section
        + "## What to do: Architecture Decision\n\n"
        "Study the system analysis and current code. Create the file "
        f"`docs/decisions/ARCHITECTURE_DECISION_{parent_key}.md`.\n\n"
        f"The file MUST start with exactly this header "
        f"(copy verbatim):\n\n"
        f"```\n{file_header}```\n\n"
        "The file should contain:\n"
        "1. **Context** — briefly, why we are making this change\n"
        "2. **Decision** — concrete architectural decision "
        "with justification. Specify WHICH files to change and HOW.\n"
        "3. **Reuse** — what existing code to use. "
        "Check the codebase with Grep!\n"
        "4. **Alternatives** — what was considered and why rejected\n"
        "5. **API contract** — new/changed endpoints, "
        "data formats\n"
        "6. **Data schema** — if models or storage change\n"
        "7. **Implementation sequence** — step-by-step order "
        "(what to do in the dev stage)\n"
        "8. **Success metrics** — how to know the task is done\n\n"
        "Important:\n"
        "- Follow project principles from CLAUDE.md and STEERING.md "
        "(if they exist)\n"
        "- Do not duplicate existing functionality — "
        "search the codebase before proposing new code\n\n"
    ).strip()


# ── Stage: development ─────────────────────────────────────────────────────────

def build_development_prompt(
    issue: dict,
    sys_analysis: str = "",
    architecture: str = "",
) -> str:
    parent_key = issue.get("parent_key", issue["key"])

    context_parts = []
    if sys_analysis:
        context_parts.append(
            "## System analysis (from previous stage)\n\n"
            + sys_analysis[:3000]
        )
    if architecture:
        context_parts.append(
            "## Architecture decision (from previous stage)\n\n"
            + architecture[:3000]
        )
    context_section = (
        ("\n\n".join(context_parts) + "\n\n") if context_parts else ""
    )

    safety_warning = ""
    if issue.get("safety_relevant"):
        safety_warning = (
            "## SAFETY-RELEVANT\n"
            "Read STEERING.md before starting (if it exists). "
            "Pay extra attention to error handling and edge cases. "
            "Prefer fail-safe defaults.\n\n"
        )

    return (
        _base_header(issue)
        + _pre_flight("development", parent_key)
        + safety_warning
        + context_section
        + _coding_standards()
        + "## What to do: Implementation\n\n"
        "Implement the task STRICTLY following the architecture decision. "
        "If no architecture decision exists — use the system analysis "
        "and task description as guidance.\n\n"
        "### Workflow\n"
        f"1. Read `docs/decisions/ARCHITECTURE_DECISION_{parent_key}.md` and "
        f"`docs/decisions/SYSTEM_ANALYSIS_{parent_key}.md` (if present).\n"
        "2. Read ARCHITECTURE.md — find related services "
        "and libraries.\n"
        "3. **Find existing code** to reuse:\n"
        "   - Grep for keywords across the codebase\n"
        "   - Look for similar functions in existing modules\n"
        "   - Do NOT create duplicates!\n"
        "4. Implement with minimal changes.\n"
        "5. Write basic tests (pytest) for the new code.\n"
        "6. Do NOT modify ARCHITECTURE.md or other shared docs "
        "— this causes merge conflicts in parallel pipelines.\n\n"
        + _test_loop()
        + _post_flight()
    ).strip()


# ── Stage: testing ─────────────────────────────────────────────────────────────

def build_testing_prompt(
    issue: dict,
    sys_analysis: str = "",
    architecture: str = "",
) -> str:
    parent_key = issue.get("parent_key", issue["key"])

    context_parts = []
    if sys_analysis:
        context_parts.append(
            "## System analysis\n\n" + sys_analysis[:2000]
        )
    if architecture:
        context_parts.append(
            "## Architecture decision\n\n" + architecture[:2000]
        )
    context_section = (
        ("\n\n".join(context_parts) + "\n\n") if context_parts else ""
    )

    return (
        _base_header(issue)
        + _pre_flight("testing", parent_key)
        + context_section
        + "## What to do: Testing\n\n"
        "### Step 1: Decide if tests are needed\n\n"
        "NOT every change needs tests. Before writing anything, evaluate:\n"
        "- Config changes, docs, minor refactors → **0 tests** (skip this stage)\n"
        "- Simple bug fix or small feature → **1-2 tests** for the core behavior\n"
        "- New API endpoint, complex logic, safety-critical code → "
        "**more tests** as appropriate\n\n"
        "If the change doesn't warrant tests, just write a brief comment "
        "in the code explaining why, and stop. Do NOT write tests "
        "just to have tests.\n\n"
        "### Step 2: Understand the changes\n"
        "1. Read the code that was changed for this task "
        f"(see `docs/decisions/ARCHITECTURE_DECISION_{parent_key}.md` → "
        "section 'Implementation sequence')\n"
        "2. Look at existing tests in `tests/` — "
        "use the same patterns and fixtures\n"
        "3. Check `tests/conftest.py` — "
        "what fixtures are already available\n\n"
        "### Step 3: Write only meaningful tests\n"
        "Focus on what matters most:\n"
        "1. **Happy path** — does the main use case work?\n"
        "2. **Edge cases** — only if there are real edge cases\n"
        "3. **Error handling** — only if the code has explicit "
        "error handling worth testing\n\n"
        "Quality over quantity. 2 good tests > 10 trivial ones.\n\n"
        "### Test rules\n"
        "- pytest, NOT unittest\n"
        "- Deterministic (no time.sleep, no random without seed)\n"
        "- Each test checks one thing\n"
        "- Names: `test_<what>_<when>_<expected_result>`\n"
        "- Reuse fixtures from conftest.py\n"
        "- Do NOT mock what you can test directly\n"
        "- Do NOT write redundant tests for trivial getters/setters\n\n"
        + _test_loop()
        + _post_flight()
    ).strip()


# ── Router ─────────────────────────────────────────────────────────────────────

def build_stage_prompt(issue: dict, artifact_context: dict) -> str:
    """Route to the correct prompt builder based on issue['stage']."""
    stage = issue.get("stage", "")
    sys_analysis = artifact_context.get("sys-analysis", "")
    architecture = artifact_context.get("architecture", "")

    if stage == "sys-analysis":
        return build_sys_analysis_prompt(issue)
    elif stage == "architecture":
        return build_architecture_prompt(
            issue, sys_analysis=sys_analysis
        )
    elif stage == "bootstrap-product-framing":
        return build_bootstrap_product_framing_prompt(issue)
    elif stage == "bootstrap-architecture-baseline":
        return build_bootstrap_architecture_baseline_prompt(issue)
    elif stage == "bootstrap-repo-scaffold":
        return build_bootstrap_repo_scaffold_prompt(issue)
    elif stage == "bootstrap-work-breakdown":
        return build_bootstrap_work_breakdown_prompt(issue)
    elif stage == "development":
        return build_development_prompt(
            issue, sys_analysis=sys_analysis,
            architecture=architecture,
        )
    elif stage == "testing":
        return build_testing_prompt(
            issue, sys_analysis=sys_analysis,
            architecture=architecture,
        )
    else:
        from orchestrator import build_claude_prompt
        return build_claude_prompt(
            issue,
            {"type": "feature", "complexity": "medium",
             "needs_tests": True, "safety_relevant": False,
             "main_files": []},
        )


# ── Planning pipeline ─────────────────────────────────────────────────────────

def build_plan_prompt(issue: dict) -> str:
    """Build prompt for the planning pipeline (PLAN: prefix tasks).

    Worker LLM reads the repo snapshot in the prompt and breaks down the feature
    into epics/tasks (JSON). Formerly **Claude Code** read files via tools.
    """
    desc_text = issue.get('description_text', '')
    summary = issue.get('summary', '')
    epic_context = issue.get('epic_context', '')

    context_section = ""
    if epic_context:
        context_section = (
            "## Project/epic context\n"
            f"{epic_context}\n\n"
        )

    return (
        "## Planning task\n\n"
        f"Feature/project: **{summary}**\n\n"
        + context_section
        + (f"## Description\n\n{desc_text}\n\n" if desc_text else "")
        + "## Mandatory reading\n\n"
        "Read these files (if they exist) to understand the project:\n"
        "1. **CLAUDE.md** — project rules and conventions\n"
        "2. **ARCHITECTURE.md** — project structure, components, "
        "dependencies\n"
        "3. **STEERING.md** — design principles, constraints\n\n"
        "## Step 1: Business analysis\n\n"
        "Before creating any tasks, evaluate the feature:\n\n"
        "1. **Does this already exist?** Search the codebase. "
        "If the feature (or something very similar) is already "
        "implemented — report that and stop.\n"
        "2. **Is this feasible?** Given the current architecture, "
        "can this be done without massive rewrites? "
        "If not — explain why and suggest alternatives.\n"
        "3. **Is this worth building?** Consider:\n"
        "   - Does it align with the project's direction "
        "(see STEERING.md, CLAUDE.md)?\n"
        "   - Is the scope reasonable or is it too vague?\n"
        "   - Are there simpler ways to achieve the same goal?\n\n"
        "If the answer to any of these is NO — return a JSON with "
        "empty epics and a rejection reason:\n\n"
        "{\n"
        '  "rejected": true,\n'
        '  "reason": "This feature already exists in src/auth/oauth.py. '
        "The current implementation covers Google and GitHub providers. "
        'No new work needed.",\n'
        '  "epics": []\n'
        "}\n\n"
        "Only proceed to Step 2 if the feature is genuinely needed.\n\n"
        "## Step 2: Break down into epics and tasks\n\n"
        "Study the codebase:\n"
        "- Understand the current architecture\n"
        "- Identify which components need changes\n"
        "- Find existing code that can be reused\n"
        "- Consider dependencies between tasks\n\n"
        "Each task should be small enough for one dev pipeline run "
        "(a few hours of coding).\n\n"
        "## Output format\n\n"
        "Reply with ONLY a JSON object (no backticks, no markdown):\n\n"
        "{\n"
        '  "rejected": false,\n'
        '  "reason": "",\n'
        '  "epics": [\n'
        "    {\n"
        '      "title": "Epic title — short and clear",\n'
        '      "description": "What this epic achieves, 2-3 sentences",\n'
        '      "tasks": [\n'
        "        {\n"
        '          "title": "Task title — actionable, starts with verb",\n'
        '          "description": "What exactly to do. Include: '
        "which files to change, what the expected behavior is, "
        'acceptance criteria. 3-5 sentences.",\n'
        '          "labels": ["domain:api", "service:backend"]\n'
        "        }\n"
        "      ]\n"
        "    }\n"
        "  ]\n"
        "}\n\n"
        "## Rules\n\n"
        "- Each epic = a logical group of related changes\n"
        "- Each task = one focused unit of work (1 PR)\n"
        "- Task titles start with a verb: "
        '"Add...", "Fix...", "Implement...", "Refactor..."\n'
        "- Task descriptions must have enough detail for the dev pipeline "
        "to implement without asking questions\n"
        "- Order tasks by dependency: independent tasks first, "
        "dependent tasks later\n"
        "- Include infrastructure/config tasks if needed "
        "(DB migrations, new configs, etc.)\n"
        "- Typically 2-5 epics, 2-6 tasks per epic\n"
        "- Do NOT include testing as a separate task — "
        "the dev pipeline adds tests automatically\n"
    ).strip()
