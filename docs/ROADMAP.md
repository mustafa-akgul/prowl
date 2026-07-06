# Roadmap

This document tracks completed milestones and the forward-looking plan for
PRowl. Items are grouped into phases; each phase is independently
shippable.

## Completed

### Phase 1 — Core Review Pipeline
- CLI orchestrator (`agent/review_agent.py`) sequencing GitHub → Jira → LLM → publish
- GitHub integration: PR diff retrieval, comment posting, Jira key extraction
- Jira integration: ticket context, comments, status transitions
- Prompt template system with `base`, `security`, and `performance` modes
- GitHub Actions triggers: automatic on PR events and `@review-agent` PM commands

### Phase 2 — Web Dashboard
- FastAPI management panel: review runner, settings, prompt editor, Kanban board
- Background jobs with live status polling and cancellation
- Review history persisted to SQLite (SQLAlchemy async)
- Post-review AI chat about the reviewed PR
- Light/dark theme, PR detail modal with rendered diffs

### Phase 3 — Scale & Knowledge
- Multi-agent review mode: specialized sub-agents (security, performance, style)
  fan out in parallel; a lead agent consolidates their findings
- Celery + Redis task queue with dedicated worker containers
- GitHub/Jira webhook ingestion with HMAC signature verification
- RAG layer (ChromaDB) injecting corporate knowledge into review prompts
- AST-based structural analysis of changed Python files
- HTML/PDF report generation and email/Slack/Teams notifications
- Docker Compose deployment (dashboard, worker, Redis, nginx)
- Local LLM support via Ollama alongside Google Gemini

## Planned

### Phase 4 — Model Intelligence & Cost Optimization

**Smart LLM routing.** Not every diff needs the strongest model. A complexity
analyzer will inspect diff size and file types and route accordingly:

| Tier | Example diff | Target model |
|------|--------------|--------------|
| Light | Docs, styling, config | Gemini Flash-class or a small local model |
| Standard | Typical feature PRs | Code-specialized local models (Qwen2.5-Coder, DeepSeek-Coder) |
| Critical | Architectural or security-sensitive changes | Strongest available API model |

**Air-gapped mode.** An `OFFLINE_MODE` flag will cut all third-party API
dependencies so the system runs fully on-premise: local Git/Jira, local
database, and Ollama-served models only. Includes model-specific prompt
formats (e.g. fill-in-the-middle) and context-window tuning.

### Phase 5 — Advanced Memory

**Custom embeddings.** A pluggable adapter in the embedding provider for
domain- or language-specific models (e.g. fine-tuned Turkish embeddings for
internal tickets and commit messages).

**Semantic caching.** When a developer re-pushes a near-identical diff (typo
fix, rebase), serve the previous review from a Redis + ChromaDB similarity
cache instead of paying for a fresh LLM call.

### Phase 6 — Continuous Learning

**Feedback collection.** Capture accept/reject/like signals on agent
suggestions from the dashboard and PR comments into a structured dataset.

**Fine-tuning export.** Convert approved reviews and rejected suggestions into
a `.jsonl` training set suitable for LoRA/QLoRA fine-tuning of a local model,
so the agent converges on the team's own review standards over time.

## Contributing

If you want to pick up a roadmap item, please open an issue first so the
approach can be discussed. See [CONTRIBUTING.md](../CONTRIBUTING.md).
