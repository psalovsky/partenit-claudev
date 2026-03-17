"""
Stage-specific Claude Code prompt builders.

Each function receives the issue dict + enrichment context and returns
a ready-to-use prompt string for `claude -p <prompt>`.

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
            "## Контекст эпика\n"
            f"{issue['epic_context']}\n\n"
        )

    desc_text = issue.get('description_text', '')
    if desc_text:
        desc_section = (
            "## Описание родительской задачи\n"
            f"{desc_text}\n\n"
        )
    else:
        desc_section = (
            "## Описание родительской задачи\n"
            "(Описание не заполнено. Ориентируйся на название задачи "
            "и контекст эпика выше.)\n\n"
        )

    return (
        f"## Задача: {issue['parent_key']} — {parent_summary}\n\n"
        f"Подзадача: {issue['key']} | "
        f"Тип этапа: **{issue['stage']}** | "
        f"Приоритет: {issue.get('priority', 'Medium')}\n"
        f"Компоненты: {', '.join(issue.get('components', []) or [])}\n\n"
        + epic_section
        + desc_section
    )


# ── Mandatory reading + coding standards ──────────────────────────────────────

def _pre_flight(stage: str, parent_key: str = "") -> str:
    """Files to read BEFORE starting any work."""
    base_files = (
        "## Обязательное чтение перед началом работы\n\n"
        "Прочитай эти файлы ПОЛНОСТЬЮ, прежде чем писать что-либо:\n\n"
        "1. **CLAUDE.md** — правила проекта, абсолютные запреты, "
        "порядок приоритетов\n"
        "2. **ARCHITECTURE.md** — карта сервисов, портов, "
        "библиотек, data flows\n"
        "3. **STEERING.md** — инварианты, границы слоёв, "
        "что НЕЛЬЗЯ нарушать\n"
    )

    if stage in ("architecture", "development", "testing"):
        sa_file = f"SYSTEM_ANALYSIS_{parent_key}.md" if parent_key else "SYSTEM_ANALYSIS*.md"
        base_files += (
            f"4. **{sa_file}** — системный анализ этой задачи "
            "(если есть в репо)\n"
        )

    if stage in ("development", "testing"):
        ad_file = (f"ARCHITECTURE_DECISION_{parent_key}.md"
                   if parent_key else "ARCHITECTURE_DECISION*.md")
        base_files += (
            f"5. **{ad_file}** — архитектурное решение этой задачи "
            "(если есть в репо)\n"
        )

    return base_files + "\n"


def _coding_standards() -> str:
    """Coding standards that apply to ALL stages."""
    return (
        "## Стандарты кода\n\n"
        "### Архитектура\n"
        "- **Не дублируй код.** Перед созданием новой функции/класса "
        "проверь, нет ли уже подходящей в `libs/` или `services/`. "
        "Используй `Grep` для поиска.\n"
        "- **Не создавай новые абстракции** без необходимости. "
        "Три одинаковые строки лучше, чем преждевременная абстракция.\n"
        "- **Libs — чистый Python**, без HTTP внутри libs. "
        "HTTP только в services.\n"
        "- **Single-file сервисы** (~300-500 строк). Если больше — "
        "split по ответственности.\n"
        "- **Не смешивай слои** L1/L2a/L2b/L3. Каждый слой — "
        "отдельный модуль.\n\n"
        "### Стиль\n"
        "- Python: `http.server.BaseHTTPRequestHandler` для новых "
        "сервисов (если сервис уже не на FastAPI).\n"
        "- Логирование: `logger = logging.getLogger('service_name')`, "
        "НЕ print().\n"
        "- Все сервисы: `GET /health` → JSON.\n"
        "- sys.path.insert для libs: "
        "`sys.path.insert(0, os.path.join(os.path.dirname(__file__), "
        "'..', '..', 'libs'))`\n"
        "- Типизация: type hints для публичных функций.\n"
        "- Docstrings: только для неочевидной логики.\n\n"
        "### Safety (нарушать НЕЛЬЗЯ)\n"
        "- L1/L2a — только детерминированный код. Без ML, LLM, "
        "network I/O.\n"
        "- Safety бинарна: ALLOW / DENY. Никогда score-based.\n"
        "- Ошибка → DENY / SAFE_FALLBACK. Fail-open запрещён.\n"
        "- Каждый reject → ReasonCode + audit_ref.\n\n"
        "### Что НЕ делать\n"
        "- НЕ рефакторь код, который не относится к задаче.\n"
        "- НЕ добавляй комментарии/docstrings к коду, который "
        "не менял.\n"
        "- НЕ добавляй error handling для невозможных сценариев.\n"
        "- НЕ создавай утилитарные хелперы для одноразовых операций.\n"
        "- НЕ добавляй feature flags или backwards-compatibility shims.\n"
        "- НЕ создавай git-коммиты — это сделает pipeline.\n\n"
    )


def _post_flight() -> str:
    """Checklist AFTER completing work."""
    return (
        "## После завершения работы\n\n"
        "1. Если добавил/изменил/удалил сервис — **обнови "
        "ARCHITECTURE.md** (секция 3 + секция 6).\n"
        "2. Если добавил новый порт — проверь что не конфликтует "
        "(см. ARCHITECTURE.md).\n"
        "3. Если заметил tech debt — добавь в TECH_DEBT.md.\n"
        "4. Непонятно → оставь TODO с объяснением, "
        "не угадывай.\n\n"
    )


def _test_loop() -> str:
    """Instructions to iterate until tests pass."""
    return (
        "## Цикл тестирования (ОБЯЗАТЕЛЬНО)\n\n"
        "После завершения кода/тестов выполни этот цикл:\n\n"
        "```\n"
        "repeat:\n"
        "  1. pytest tests/unit/ -x -v\n"
        "  2. если всё зелёное → СТОП, работа завершена\n"
        "  3. если есть FAIL/ERROR → прочитай traceback\n"
        "  4. определи причину: баг в коде или неверное ожидание в тесте\n"
        "  5. исправь ПРИЧИНУ — если баг в коде, чини код, "
        "не подгоняй тест под баг\n"
        "  6. goto 1\n"
        "```\n\n"
        "Максимум 5 итераций. Если после 5 итераций тесты "
        "всё ещё падают — оставь TODO с описанием проблемы.\n\n"
        "ВАЖНО: не удаляй падающие тесты! Чини код или "
        "исправляй тест, если ожидание неверное.\n\n"
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
        f"# Системный анализ: [{parent_key}]({parent_url})"
        f" — {parent_summary}\n\n"
        f"> **Jira:** [{parent_key}]({parent_url}) · "
        f"Подзадача: [{issue['key']}]({subtask_url})  \n"
        f"> **Этап:** sys-analysis  \n"
        "> Сгенерировано автоматически Trust Layer Pipeline\n\n"
        "---\n\n"
    )

    return (
        _base_header(issue)
        + _pre_flight("sys-analysis", parent_key)
        + "## Что нужно сделать: Системный анализ\n\n"
        "Проведи системный анализ задачи. Прочитай код затронутых "
        "компонентов, пойми текущее состояние, и создай файл "
        f"`SYSTEM_ANALYSIS_{parent_key}.md` в корне репозитория.\n\n"
        f"Файл ДОЛЖЕН начинаться ровно с этого заголовка "
        f"(скопируй дословно):\n\n"
        f"```\n{file_header}```\n\n"
        "Затем добавь разделы:\n"
        "1. **Краткое описание проблемы** — что именно требуется\n"
        "2. **Текущее состояние кода** — как это работает сейчас "
        "(прочитай реальный код, не угадывай!)\n"
        "3. **Затронутые компоненты** — список сервисов/библиотек "
        "с путями к файлам\n"
        "4. **Зависимости** — upstream/downstream, кто вызывает, "
        "кого вызывает\n"
        "5. **Существующие утилиты** — что уже есть в libs/ и можно "
        "переиспользовать (проверь через Grep!)\n"
        "6. **Риски** — потенциальные проблемы при реализации\n"
        "7. **Граничные случаи** — нестандартные ситуации\n"
        "8. **Рекомендованный подход** — конкретные шаги реализации "
        "с указанием файлов\n\n"
        "Формат: markdown, списки, примеры кода где нужно.\n"
        "Объём: 200-500 строк — подробно, но по делу.\n\n"
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
        f"# Архитектурное решение: [{parent_key}]({parent_url})"
        f" — {parent_summary}\n\n"
        f"> **Jira:** [{parent_key}]({parent_url}) · "
        f"Подзадача: [{issue['key']}]({subtask_url})  \n"
        f"> **Этап:** architecture  \n"
        "> Сгенерировано автоматически Trust Layer Pipeline\n\n"
        "---\n\n"
    )

    context_section = ""
    if sys_analysis:
        context_section = (
            "## Результат системного анализа (предыдущий этап)\n\n"
            f"{sys_analysis[:4000]}\n\n"
        )

    return (
        _base_header(issue)
        + _pre_flight("architecture", parent_key)
        + context_section
        + "## Что нужно сделать: Архитектурное решение\n\n"
        "Изучи системный анализ и текущий код. Создай файл "
        f"`ARCHITECTURE_DECISION_{parent_key}.md` в корне репозитория.\n\n"
        f"Файл ДОЛЖЕН начинаться ровно с этого заголовка "
        f"(скопируй дословно):\n\n"
        f"```\n{file_header}```\n\n"
        "Файл должен содержать:\n"
        "1. **Контекст** — кратко, почему мы делаем это изменение\n"
        "2. **Решение** — конкретное архитектурное решение "
        "с обоснованием. Укажи КАКИЕ ИМЕННО файлы менять и КАК.\n"
        "3. **Переиспользование** — что из существующего кода "
        "использовать (libs/, существующие хелперы). "
        "Проверь через Grep!\n"
        "4. **Альтернативы** — что рассматривалось и почему отклонено\n"
        "5. **API контракт** — новые/изменённые эндпоинты, "
        "форматы данных\n"
        "6. **Схема данных** — если меняются модели или хранилища\n"
        "7. **Последовательность** — порядок реализации "
        "(что делать в dev-этапе, пошагово)\n"
        "8. **Метрики успеха** — как понять что задача выполнена\n\n"
        "Важно:\n"
        "- Учитывай принципы Trust Layer из CLAUDE.md и STEERING.md\n"
        "- L1/L2a — детерминированные, синхронные, fail-closed\n"
        "- Не смешивай слои L1, L2a, L2b, L3\n"
        "- Не дублируй существующую функциональность — "
        "проверь libs/ перед тем как предлагать новый код\n\n"
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
            "## Системный анализ (из предыдущего этапа)\n\n"
            + sys_analysis[:3000]
        )
    if architecture:
        context_parts.append(
            "## Архитектурное решение (из предыдущего этапа)\n\n"
            + architecture[:3000]
        )
    context_section = (
        ("\n\n".join(context_parts) + "\n\n") if context_parts else ""
    )

    safety_warning = ""
    if issue.get("safety_relevant"):
        safety_warning = (
            "## SAFETY-RELEVANT\n"
            "Прочитай STEERING.md перед работой. "
            "L1/L2a — без ML, без network I/O. Fail-closed. "
            "audit_ref обязателен.\n\n"
        )

    return (
        _base_header(issue)
        + _pre_flight("development", parent_key)
        + safety_warning
        + context_section
        + _coding_standards()
        + "## Что нужно сделать: Реализация\n\n"
        "Реализуй задачу СТРОГО по архитектурному решению. "
        "Если архитектурного решения нет — ориентируйся на "
        "системный анализ и описание задачи.\n\n"
        "### Алгоритм работы\n"
        f"1. Прочитай `ARCHITECTURE_DECISION_{parent_key}.md` и "
        f"`SYSTEM_ANALYSIS_{parent_key}.md` (если есть в репо).\n"
        "2. Прочитай ARCHITECTURE.md — найди связанные сервисы "
        "и библиотеки.\n"
        "3. **Найди существующий код** для переиспользования:\n"
        "   - Grep по ключевым словам в `libs/`\n"
        "   - Grep по похожим функциям в `services/`\n"
        "   - НЕ СОЗДАВАЙ дубликаты!\n"
        "4. Реализуй минимальными изменениями.\n"
        "5. Напиши базовые тесты (pytest) для нового кода.\n"
        "6. Обнови ARCHITECTURE.md если добавил/изменил сервис "
        "или эндпоинт.\n\n"
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
            "## Системный анализ\n\n" + sys_analysis[:2000]
        )
    if architecture:
        context_parts.append(
            "## Архитектурное решение\n\n" + architecture[:2000]
        )
    context_section = (
        ("\n\n".join(context_parts) + "\n\n") if context_parts else ""
    )

    return (
        _base_header(issue)
        + _pre_flight("testing", parent_key)
        + context_section
        + "## Что нужно сделать: Тестирование\n\n"
        "Напиши исчерпывающие тесты для реализованных изменений.\n\n"
        "### Перед написанием тестов\n"
        "1. Прочитай код, который был изменён в рамках задачи "
        f"(см. `ARCHITECTURE_DECISION_{parent_key}.md` → "
        "секция «Последовательность»)\n"
        "2. Посмотри существующие тесты в `tests/` — "
        "используй те же паттерны и fixtures\n"
        "3. Проверь `tests/conftest.py` — "
        "какие fixtures уже есть\n\n"
        "### Что должно быть покрыто\n"
        "1. **Happy path** — стандартное использование\n"
        "2. **Edge cases** — граничные значения, пустые входы\n"
        "3. **Error cases** — некорректные входные данные\n"
        "4. **Safety invariants** — если safety-relevant: "
        "тесты на fail-closed\n\n"
        "### Правила для тестов\n"
        "- pytest, НЕ unittest\n"
        "- Детерминированные (нет time.sleep, нет random без seed)\n"
        "- Каждый тест проверяет одну вещь\n"
        "- Имена: `test_<что>_<когда>_<ожидаемый результат>`\n"
        "- Переиспользуй fixtures из conftest.py\n"
        "- НЕ мокируй то, что можно протестировать напрямую\n\n"
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
