# Dashboard REST API

Base URL: `http://localhost:8080`. Interactive OpenAPI docs are available at
`/docs` (Swagger UI) and `/redoc` while the dashboard is running.

## Pages

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Review page |
| GET | `/board` | PR Kanban board |
| GET | `/settings` | Settings page |
| GET | `/prompts` | Prompt template editor |

## Reviews

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/review` | Start a review job. Body: `{"pr_number": 42, "mode": "base\|security\|performance\|multi-agent", "pm_instructions": "..."}`. Returns `{"success": true, "job_id": "..."}` |
| GET | `/api/review/status/{job_id}` | Poll job status: `queued` / `running` / `completed` / `error` / `cancelled`; `result` contains the review on completion |
| POST | `/api/review/cancel/{job_id}` | Cancel a running job |
| GET | `/api/history` | Recent reviews (SQLite-backed), newest first |
| POST | `/api/chat` | Ask a follow-up question about a reviewed PR. Body: `{"pr_number": 42, "message": "..."}` |

## GitHub

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/github/pulls` | Open PRs for the active repository |
| GET | `/api/github/repos` | Repositories accessible with the configured token |
| POST | `/api/github/switch-repo` | Change the active repository. Body: `{"repo": "org/name"}` |
| GET | `/api/github/pr/{pr_number}` | PR detail: metadata, files, reviews, labels |
| GET | `/api/github/pr/{pr_number}/diff` | Raw unified diff for rendering |

## Jira

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/jira/ticket/{ticket_key}` | Ticket summary, description, acceptance criteria, status |

## Settings & prompts

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/settings` | Current settings (secrets masked) |
| POST | `/api/settings` | Save settings; values are synced to process environment |
| GET | `/api/prompts` | All prompt templates keyed by mode |
| GET | `/api/prompts/{mode}` | Single template |
| POST | `/api/prompts` | Save a template. Body: `{"mode": "base", "content": "..."}` |

## Status & statistics

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/status` | Connectivity check for Gemini/GitHub/Jira |
| GET | `/api/webhooks/status` | Webhook endpoint configuration state |
| GET | `/api/stats` | Review/board statistics |
| GET | `/api/stats/tokens` | Aggregated token usage from review history |
| GET | `/api/board/columns` | Kanban columns: open / in-review / merged / closed PRs |

## Webhooks (inbound)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/webhooks/github` | GitHub events (PR opened/synchronized, comments). Verified with HMAC-SHA256 via `GITHUB_WEBHOOK_SECRET`; processed asynchronously by Celery |
| POST | `/api/webhooks/jira` | Jira events (e.g. status changes) |

### Example: start a review and poll it

```bash
JOB=$(curl -s -X POST localhost:8080/api/review \
  -H 'Content-Type: application/json' \
  -d '{"pr_number": 42, "mode": "security"}' | jq -r .job_id)

curl -s localhost:8080/api/review/status/$JOB | jq .status
```
