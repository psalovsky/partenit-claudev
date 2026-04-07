# Partenit Claudev — инструкция по установке и работе

Этот документ фиксирует актуальное состояние проекта и шаги развёртывания.  
**Claudev** — отдельный репозиторий; не путать с другими проектами (например Trust Layer): свой **workspace в Cursor** = папка **`partenit-claudev`** на диске.

---

## 1. Что делает приложение

- Сервис на **FastAPI** принимает **webhook из Jira** (и опционально **Telegram**).
- Клонирует целевой репозиторий **GitHub** (`GITHUB_REPO`), вызывает **LLM по HTTP**, коммитит изменения, открывает **PR** в ветку `STAGE_BRANCH` (часто `stage`), при статусе **Ready to Merge** может **мержить** PR.
- **Два слоя LLM:**
  - **Оркестратор** — `LLM_BASE_URL` + `LLM_MODEL`: лёгкие задачи (ADF→текст, лейблы, краткий разбор после изменений).
  - **Воркер** — `WORKER_LLM_*` (по умолчанию тот же **DeepSeek**, модель **`deepseek-chat`**): стадии PLAN, system analysis, architecture, development, testing.
- Раньше тяжёлые стадии шли через **Claude Code CLI**; в коде оставлен **закомментированный** legacy-блок в `worker.py` для возврата к более «агентной» модели при необходимости.

Проверка «пришёл ли код»: для стадий development/testing смотрится **`git diff` / новые файлы** в клоне; если изменений нет — комментарий в Jira и PR не создаётся.

---

## 2. Workspace в Cursor

- Достаточно **отдельной папки** на диске с клоном репозитория.
- В Cursor: **Файл → Открыть папку** → выбрать каталог **`partenit-claudev`**.
- Отдельный «проект» в Cursor создавать не обязательно — важно открыть **именно эту папку**, чтобы `.env` и код относились к Claudev, а не к другому репо.

---

## 3. Требования

- **Python 3.11+** (или **Docker**).
- Учётные записи и ключи: **Jira Cloud**, **GitHub** (fine-grained или classic с правами на Contents + Pull requests), **DeepSeek** (или другой OpenAI-совместимый API для `LLM_*` / `WORKER_LLM_*`).
- Для работы **круглосуточно** сервис должен быть запущен на **хосте с публичным HTTPS** (VPS, **Railway**, и т.д.); достаточно держать включённым **не** домашний ПК, если приложение в облаке.

---

## 4. Установка локально

```bash
cd partenit-claudev
cp .env.example .env
# Отредактируйте .env — см. раздел 5
pip install -r requirements.txt
python main.py
```

Проверка здоровья: `http://127.0.0.1:8090/health` (порт задаётся **`PORT`** в `.env`, по умолчанию **8090**).

**Загрузка `.env`:** при `import config` вызывается **`load_dotenv`** для файла **`.env`** в корне проекта (см. `config.py`). Не коммитьте `.env` в Git.

---

## 5. Переменные окружения (`.env`)

Ориентир — **`.env.example`** в корне репозитория. Минимум:

| Назначение | Переменные |
|------------|------------|
| Webhook | `WEBHOOK_SECRET` — случайная строка; тот же секрет в URL Jira webhook |
| Оркестратор LLM | `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` (для DeepSeek: `https://api.deepseek.com`, `deepseek-chat`) |
| Воркер LLM | Обычно не дублировать: используется `LLM_API_KEY` / `LLM_BASE_URL`; при необходимости `WORKER_LLM_API_KEY`, `WORKER_LLM_BASE_URL`, `WORKER_LLM_MODEL` |
| Jira | `JIRA_DOMAIN`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_PROJECT_KEY` |
| GitHub | `GITHUB_TOKEN`, `GITHUB_TOKEN_TARGET`, `GITHUB_REPO` (`owner/repo` — **целевой** репозиторий кода) |

Имена статусов Jira должны совпадать с workflow или переопределены через `STATUS_*` в `.env`.

**Ключ DeepSeek** не хранить в публичных файлах; в `.env.example` только плейсхолдеры.

---

## 6. Проверка API LLM без пайплайна

В корне проекта:

```bash
python smoke_llm.py
```

Ожидается **HTTP 200**, короткий ответ модели и блок `usage`. Ошибка **402 / Insufficient Balance** — пополнить баланс на стороне провайдера.

---

## 7. Docker

```bash
docker compose up --build
```

`docker-compose.yml` подключает **`env_file: .env`** и том для `/tmp/pipeline-work`. Образ без Node/Claude CLI — только Python и git (см. `Dockerfile`).

---

## 8. Jira webhook

- URL: `https://<ваш-хост>/webhook/jira?secret=<WEBHOOK_SECRET>`
- Событие: **issue updated**
- JQL: ограничение по проекту (например `project = MYPROJECT`)

Локальный `127.0.0.1` из облака Jira недоступен — нужен публичный URL (деплой или туннель).

---

## 9. Telegram (опционально)

- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- Один раз выставить webhook:  
  `https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://<хост>/webhook/telegram`

---

## 10. Где «пишется код»

- Код **продукта** не в репозитории Claudev, а в **`GITHUB_REPO`**.
- Во время job рабочая копия: **`/tmp/pipeline-work/<job_id>`** (в Docker — смонтированный том).
- Ветки: обычно **`feature/<ключ-родительской-задачи-jira>`**, PR в **`STAGE_BRANCH`**.

---

## 11. Полезные HTTP-эндпоинты

- `GET /health` — статус, jobs, очередь пайплайнов  
- `GET /jobs`, `GET /queue`  
- `POST /jobs/{job_id}/cancel`  
- `POST /webhook/jira`, `POST /webhook/telegram`

---

## 12. Хостинг и стоимость (ориентир)

Платформы вроде **Railway** обычно берут **подписку + usage** (RAM/CPU/трафик); точные цены — на [официальном сайте Railway](https://railway.app) / документации. LLM оплачивается отдельно у провайдера (например DeepSeek).

---

## 13. Ссылки на код

- Воркер LLM и legacy Claude: `worker.py`  
- Оркестратор: `orchestrator.py`  
- Конфиг и `WORKER_*`: `config.py`  
- Точка входа: `main.py`  
- Пример env: `.env.example`  
- Smoke-тест LLM: `smoke_llm.py`

---

*Файл можно обновлять по мере изменений репозитория.*
