# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- Rebranded the project as **PRowl** 🦉
- Dashboard UI translated from Turkish to English
- Documentation set rewritten in English (README, architecture, API reference, roadmap)
- Codebase formatted and linted with Ruff; project metadata moved to `pyproject.toml`

### Added
- `GET /health` liveness endpoint (the docker-compose healthcheck previously targeted a nonexistent route)

### Fixed
- Declared previously missing runtime dependencies (`sqlalchemy`, `aiosqlite`, `nest_asyncio`)

### Removed
- One-off scaffolding scripts (`apply_plan.py`, `fix_history.py`) and the legacy Turkish plan document

## [3.0.0] — 2026-03-10

### Added
- **Multi-agent review mode**: security/performance/style sub-agents run in
  parallel; a lead agent consolidates results
- Celery + Redis task queue with a dedicated worker container
- GitHub and Jira webhook endpoints with HMAC-SHA256 signature verification
- RAG layer (ChromaDB) for injecting corporate knowledge into prompts,
  with pluggable embedding providers
- AST-based structural analysis of changed Python files
- HTML/PDF report generation (WeasyPrint) and email/Slack/Teams notifications
- Local LLM support via Ollama, with provider selection and fallback model in Settings
- Post-review AI chat panel scoped to the reviewed PR
- Docker Compose stack: dashboard, worker, Redis, nginx reverse proxy

### Changed
- Review history moved from JSONL to SQLite (async SQLAlchemy)

## [2.0.0] — 2026-02-26

### Added
- FastAPI web dashboard: review runner with live job status and cancellation,
  settings panel, prompt template editor, PR Kanban board, token statistics
- Config persistence in `.data/config.json`, synced to environment on save
- Light/dark theme, PR detail modal with rendered diffs

### Changed
- Switched the primary LLM from Anthropic Claude to Google Gemini

## [1.0.0] — 2026-02-25

### Added
- Core review pipeline: GitHub PR diff → Jira context → LLM review → PR comment + Jira update
- Review modes: `base`, `security`, `performance` with editable prompt templates
- GitHub Actions triggers: automatic PR review and `@review-agent` PM commands
- Jira integration: ticket context, acceptance criteria field, status transition
- Mock-based pytest suite
