# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Purpose

**PRowl** — an AI code review agent that reviews GitHub PRs using Google Gemini or local Ollama models. Reviews are triggered from a web dashboard, the CLI, GitHub Actions, `@review-agent` PR comments, or GitHub/Jira webhooks. Results are posted as PR comments, Jira updates, HTML/PDF reports, and email/Slack/Teams notifications.

## Commands

```bash
# Install dependencies (+ dev tools)
pip install -r requirements.txt
pip install -e ".[dev]"

# Run CLI review
python -m agent.review_agent --pr 42 --mode base
python -m agent.review_agent --pr 42 --mode security --instructions "Focus on auth flows"

# Start web dashboard (port 8080)
python main.py --port 8080 --reload

# Full stack (dashboard + Celery worker + Redis + nginx)
docker-compose up -d

# Tests / lint / format
pytest tests/ -v
ruff check agent/ tests/ main.py
ruff format agent/ tests/ main.py
```

## Architecture

Two execution paths:

1. **Synchronous**: CLI / GitHub Actions run `review_agent.py` in-process — GitHub → Jira → RAG/AST context → LLM → publish.
2. **Asynchronous**: `dashboard.py` (FastAPI) and `webhook_handler.py` dispatch Celery tasks through Redis to `worker.py`. The `multi-agent` mode fans out security/performance/style sub-agents in parallel and a lead agent consolidates results (Celery chord).

**LLM clients**: `BaseLLMClient` (abstract, `base_client.py`) → `GeminiClient` and `OllamaClient`. The base class owns template loading, prompt preparation, retry-delay parsing, and diff chunking. Provider selection (`provider` key) and Gemini fallback model are configured in Settings.

**Configuration**: `.data/config.json` (auto-created). `_apply_config_to_env()` in `dashboard.py` syncs config to environment variables on save, so client modules (which use `os.getenv`) pick up changes without restart. `.env` is the initial seed.

**Persistence**: Review history lives in SQLite (`.data/review_agent.db`) via async SQLAlchemy (`db.py`, `history_manager.py` — uses `nest_asyncio` to bridge sync callers). ChromaDB vector data persists under `.data/`. Dashboard `job_statuses` dict is in-memory only.

**On Windows**, `celery_app.py` swaps Redis for a SQLite broker/backend so local development works without Redis.

## Module Summary

| Module | Description |
|--------|-------------|
| `agent/review_agent.py` | Review orchestrator (CLI + pipeline) |
| `agent/dashboard.py` | FastAPI web app (pages + REST API, see docs/API.md) |
| `agent/base_client.py` | Abstract LLM client: templates, chunking, retry parsing |
| `agent/gemini_client.py` / `agent/ollama_client.py` | LLM provider implementations |
| `agent/github_client.py` / `agent/jira_client.py` | Integrations |
| `agent/celery_app.py` / `agent/worker.py` | Task queue config + tasks (incl. multi-agent chord) |
| `agent/webhook_handler.py` | GitHub/Jira webhooks with HMAC-SHA256 verification |
| `agent/rag/` | ChromaDB vector store, embedding providers, context retriever |
| `agent/ast_analyzer.py` | AST-based structural context for changed Python files |
| `agent/report_generator.py` / `agent/notifier.py` | HTML/PDF reports; email/Slack/Teams delivery |
| `agent/db.py` / `agent/history_manager.py` | Async SQLAlchemy review history |
| `agent/config_manager.py` | Settings + `MODE_TO_FILE` prompt registry |
| `agent/prompts/` | Prompt templates (`{jira_context}`, `{pm_instructions}`, `{diff}`) |

## Environment Variables

Core: `GEMINI_API_KEY`, `GITHUB_TOKEN`, `GITHUB_REPO`. Jira: `JIRA_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_REVIEW_STATUS`, `JIRA_AC_FIELD`. Async/webhooks: `REDIS_URL`, `GITHUB_WEBHOOK_SECRET`. Notifications: `SMTP_*`, `SLACK_WEBHOOK_URL`, `TEAMS_WEBHOOK_URL`. See `.env.example`.

## Code Conventions

- `from __future__ import annotations` at the top of every module
- Google-style docstrings (`Args`/`Returns`/`Raises`) on public functions and classes
- Custom exception per module (`JiraError`, `GitHubError`, `GeminiError`) inheriting from `LLMError`
- **Everything is in English** — code, docstrings, logs, prompts, and UI text
- Ruff for lint + format; config in `pyproject.toml` (E402 per-file-ignores are deliberate)

## Test Conventions

- No `conftest.py` — each test file defines its own `mock_env` fixture
- Patch the **consumer's import path** (e.g. `agent.review_agent.GeminiClient`, not `agent.gemini_client.GeminiClient`)
- pytest config lives in `pyproject.toml` (`testpaths = ["tests"]`); no network in tests

## Known Constraints and Gotchas

- **Jira Cloud only**: `JiraClient` hardcodes `cloud=True`
- **`JIRA_AC_FIELD` has no Settings UI** — set via `.env` or `.data/config.json`
- `history_manager.py` bridges async SQLAlchemy into sync callers with `nest_asyncio`; be careful when touching event-loop code

## Adding a New Review Mode

1. Create a template under `agent/prompts/` with the standard placeholders
2. Register it in `MODE_TO_FILE` in `agent/config_manager.py`
3. Add the mode to `--mode` choices in `agent/review_agent.py`
4. Extend the grep pattern in `.github/workflows/pm-command.yml`
5. Add at least one test in `tests/`
