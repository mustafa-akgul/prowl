"""Dashboard module — full-featured web management panel.

Provides a FastAPI-based web interface for managing the review agent.
All settings (API keys, prompt templates, review configuration) can be
edited through the panel.

Pages:
    /               → Review Dashboard (home)
    /settings       → API & configuration settings
    /prompts        → Prompt template editor
    /board          → Kanban board

API Endpoints:
    POST /api/review        → Start a review
    GET  /api/history       → Review history
    GET  /api/settings      → Get settings
    POST /api/settings      → Save settings
    GET  /api/prompts       → Get prompts
    POST /api/prompts       → Save prompt
    GET  /api/status        → Check connection status

Run with:
    uvicorn agent.dashboard:app --reload --port 8080
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from agent.config_manager import (
    aget_all_prompts,
    aload_config,
    aload_prompt,
    asave_config,
    asave_prompt,
    get_api_config,
    get_safe_api_config,
)
from agent.github_client import GitHubClient
from agent.history_manager import load_history, save_review

logger = logging.getLogger(__name__)

# --- Paths ---
BASE_DIR = Path(__file__).parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

# --- Constants ---
REVIEW_HISTORY_LIMIT = 50
TOP_CONTRIBUTORS_LIMIT = 5
BOARD_CLOSED_PR_LIMIT = 15
MAX_JOB_HISTORY = 100
CACHE_TTL_SECONDS = 60  # TTL for cached API responses (F22)


# --- FastAPI App ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Syncs .env into config.json, then pushes config to env vars.

    Note: _sync_dotenv_to_config and _apply_config_to_env are synchronous
    but run only once at startup, so async wrapping is unnecessary (F12).
    """
    # F8: Increase thread pool to avoid starvation during concurrent
    # reviews with rate-limit waits (time.sleep in _generate_with_retries).
    import concurrent.futures

    loop = asyncio.get_event_loop()
    loop.set_default_executor(concurrent.futures.ThreadPoolExecutor(max_workers=8))

    _sync_dotenv_to_config()
    _apply_config_to_env()
    yield


app = FastAPI(
    title="AI Code Review Agent",
    description="Web panel for automated code review powered by Gemini AI",
    version="3.0.0",
    lifespan=lifespan,
)

# Include webhook router
from agent.webhook_handler import router as webhook_router

app.include_router(webhook_router)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# --- Review History ---
review_history: list[dict] = load_history(limit=REVIEW_HISTORY_LIMIT)
_history_lock = threading.Lock()

# --- Background Job Statuses ---
# { "job_id": { "status": "processing" | "completed" | "error", "result": ..., "error": ... } }
job_statuses: dict[str, dict] = {}


def _cleanup_old_jobs() -> None:
    """Removes completed/errored/cancelled jobs when dict exceeds MAX_JOB_HISTORY."""
    if len(job_statuses) <= MAX_JOB_HISTORY:
        return
    removable = [
        k for k, v in job_statuses.items() if v.get("status") in ("completed", "error", "cancelled")
    ]
    for k in removable[: len(removable) - MAX_JOB_HISTORY // 2]:
        del job_statuses[k]


# --- GitHubClient Singleton ---
_gh_client: GitHubClient | None = None


def _get_cached_gh() -> GitHubClient:
    """Returns a cached GitHubClient instance (lazy singleton).

    Avoids re-creating the client (and re-authenticating) on every request.
    Invalidated by _invalidate_gh_cache() when settings change.
    """
    global _gh_client
    if _gh_client is None:
        _gh_client = GitHubClient()
    return _gh_client


def _invalidate_gh_cache() -> None:
    """Clears the GitHubClient singleton so it is re-created on next use."""
    global _gh_client
    _gh_client = None


# --- TTL Response Cache (F22) ---
_response_cache: dict[str, dict] = {}


def _get_cached(key: str) -> Any | None:
    """Returns cached response if within TTL, else None."""
    import time

    entry = _response_cache.get(key)
    if entry and (time.time() - entry["time"]) < CACHE_TTL_SECONDS:
        return entry["data"]
    return None


def _set_cache(key: str, data: Any) -> None:
    """Stores a response in the TTL cache."""
    import time

    _response_cache[key] = {"data": data, "time": time.time()}


# ─── Schemas ───


class ReviewRequest(BaseModel):
    """Request schema for starting a review."""

    pr_number: int = Field(..., ge=1, description="Pull request number")
    mode: Literal["base", "security", "performance", "multi-agent"] = Field(
        default="base", description="Review mode"
    )
    pm_instructions: str | None = Field(default=None, description="Optional PM instructions")


class ReviewResponse(BaseModel):
    """Response schema for a review start request."""

    success: bool
    job_id: str | None = None
    error: str | None = None


class ChatRequest(BaseModel):
    """Request schema for conversational review chat."""

    pr_number: int = Field(..., ge=1, description="Pull request number")
    message: str = Field(..., min_length=1, description="Chat message")


class SettingsRequest(BaseModel):
    """Request schema for updating settings."""

    api: dict[str, str] | None = None
    review: dict[str, Any] | None = None


class PromptSaveRequest(BaseModel):
    """Request schema for saving a prompt template."""

    mode: str
    content: str


# ──────────────────────────────────────────────
# Page Routes
# ──────────────────────────────────────────────


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe used by container healthchecks."""
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def page_dashboard(request: Request) -> HTMLResponse:
    """Renders the review dashboard page."""
    return templates.TemplateResponse("index.html", {"request": request, "page": "review"})


@app.get("/settings", response_class=HTMLResponse)
async def page_settings(request: Request) -> HTMLResponse:
    """Renders the settings page."""
    return templates.TemplateResponse("index.html", {"request": request, "page": "settings"})


@app.get("/prompts", response_class=HTMLResponse)
async def page_prompts(request: Request) -> HTMLResponse:
    """Renders the prompt template editor page."""
    return templates.TemplateResponse("index.html", {"request": request, "page": "prompts"})


@app.get("/board", response_class=HTMLResponse)
async def page_board(request: Request) -> HTMLResponse:
    """Renders the Kanban board page."""
    return templates.TemplateResponse("index.html", {"request": request, "page": "board"})


# ──────────────────────────────────────────────
# API Routes — Review
# ──────────────────────────────────────────────


@app.post("/api/review", response_model=ReviewResponse)
async def api_review(req: ReviewRequest) -> ReviewResponse:
    """Starts a review job in the background."""
    _cleanup_old_jobs()
    job_id = str(uuid.uuid4())
    job_statuses[job_id] = {"status": "processing", "pr_number": req.pr_number}

    task = asyncio.create_task(
        run_background_review(job_id, req.pr_number, req.mode, req.pm_instructions)
    )
    job_statuses[job_id]["_task"] = task

    return ReviewResponse(success=True, job_id=job_id)


async def run_background_review(
    job_id: str, pr_number: int, mode: str, pm_instructions: str | None
) -> None:
    """Helper that runs a review in the background.

    Args:
        job_id: Unique job identifier.
        pr_number: Pull request number.
        mode: Review mode.
        pm_instructions: Optional PM instructions.
    """
    try:
        if mode == "multi-agent":
            from agent.review_agent import run_multi_agent

            await asyncio.to_thread(
                run_multi_agent,
                pr_number=pr_number,
                pm_instructions=pm_instructions,
            )
            # For multi-agent, the chord handles publishing. Mark as completed.
            if job_statuses.get(job_id, {}).get("status") == "cancelled":
                return
            job_statuses[job_id].update(
                {
                    "status": "completed",
                    "result": {
                        "pr_number": pr_number,
                        "mode": "multi-agent",
                        "review": "Multi-agent review chord triggered. Results will be posted on the PR.",
                    },
                }
            )
            return

        from agent.review_agent import run as run_review

        result = await asyncio.to_thread(
            run_review,
            pr_number=pr_number,
            mode=mode,
            pm_instructions=pm_instructions,
        )

        # Discard result if cancelled while the thread was running
        if job_statuses.get(job_id, {}).get("status") == "cancelled":
            return

        if result is None:
            job_statuses[job_id].update(
                {
                    "status": "error",
                    "error": "Review could not be completed. Check the logs.",
                }
            )
            return

        review_text, usage_stats = result
        record = save_review(pr_number, mode, review_text, usage_stats=usage_stats)
        with _history_lock:
            review_history.insert(0, record)
            if len(review_history) > REVIEW_HISTORY_LIMIT:
                review_history.pop()

        job_statuses[job_id].update({"status": "completed", "result": record})

    except asyncio.CancelledError:
        job_statuses[job_id]["status"] = "cancelled"

    except Exception as exc:
        logger.error("Background review error: %s", exc)
        job_statuses[job_id].update({"status": "error", "error": str(exc)})


@app.get("/api/review/status/{job_id}")
async def get_review_status(job_id: str) -> dict[str, Any]:
    """Returns the status of a background review job.

    Args:
        job_id: Job identifier returned by POST /api/review.
    """
    status = job_statuses.get(job_id)
    if not status:
        return {"success": False, "error": "Invalid job ID"}
    return {"success": True, **{k: v for k, v in status.items() if not k.startswith("_")}}


@app.post("/api/review/cancel/{job_id}")
async def cancel_review(job_id: str) -> dict[str, Any]:
    """Cancels an in-progress review job.

    Args:
        job_id: Job identifier returned by POST /api/review.
    """
    status = job_statuses.get(job_id)
    if not status:
        return {"success": False, "error": "Invalid job ID"}
    if status.get("status") != "processing":
        return {"success": False, "error": "Job is not in processing state"}

    # Mark cancelled immediately so the thread result is discarded on return
    status["status"] = "cancelled"

    task = status.get("_task")
    if task and not task.done():
        task.cancel()

    return {"success": True}


@app.get("/api/history")
async def api_history() -> list[dict]:
    """Returns the review history with token stats."""
    return review_history


@app.post("/api/chat")
async def api_chat(req: ChatRequest) -> dict[str, Any]:
    """Sends a chat message in the context of a PR and returns the AI reply."""
    try:
        from agent.review_agent import run_chat

        result = await asyncio.to_thread(run_chat, req.pr_number, req.message)
        return {"success": True, "reply": result.get("reply", "")}
    except Exception as exc:
        logger.error("Chat error: %s", exc)
        return {"success": False, "error": str(exc)}


@app.get("/api/webhooks/status")
async def api_webhooks_status() -> dict[str, Any]:
    """Returns webhook configuration status and recent events."""
    import os

    config = await aload_config()
    api = config.get("api", {})

    github_webhook_secret = bool(os.getenv("GITHUB_WEBHOOK_SECRET", ""))
    webhook_url = "/api/webhooks/github"

    return {
        "success": True,
        "webhooks": {
            "github": {
                "endpoint": webhook_url,
                "secret_configured": github_webhook_secret,
                "status": "active" if api.get("github_token") else "inactive",
            },
            "jira": {
                "endpoint": "/api/webhooks/jira",
                "status": "active"
                if api.get("jira_url") and api.get("jira_api_token")
                else "inactive",
            },
        },
    }


@app.get("/api/stats/tokens")
async def api_token_stats() -> dict[str, Any]:
    """Returns aggregated token usage statistics from review history."""
    try:
        from sqlalchemy import func, select

        from agent.db import AsyncSessionLocal, ReviewRecord, init_db

        await init_db()

        async with AsyncSessionLocal() as session:
            stmt = select(
                func.sum(ReviewRecord.prompt_tokens).label("total_prompt"),
                func.sum(ReviewRecord.completion_tokens).label("total_completion"),
                func.count(ReviewRecord.id).label("total_reviews"),
            )
            result = await session.execute(stmt)
            row = result.one()
            total_prompt = row.total_prompt or 0
            total_completion = row.total_completion or 0
            total_tokens = total_prompt + total_completion
            total_reviews = row.total_reviews or 0

            # Per-mode breakdown
            mode_stmt = select(
                ReviewRecord.mode,
                func.sum(ReviewRecord.prompt_tokens).label("prompt"),
                func.sum(ReviewRecord.completion_tokens).label("completion"),
                func.count(ReviewRecord.id).label("count"),
            ).group_by(ReviewRecord.mode)
            mode_result = await session.execute(mode_stmt)
            modes = [
                {
                    "mode": r.mode,
                    "prompt_tokens": r.prompt or 0,
                    "completion_tokens": r.completion or 0,
                    "total_tokens": (r.prompt or 0) + (r.completion or 0),
                    "count": r.count or 0,
                }
                for r in mode_result.all()
            ]

        return {
            "success": True,
            "tokens": {
                "total_prompt_tokens": total_prompt,
                "total_completion_tokens": total_completion,
                "total_tokens": total_tokens,
                "total_reviews": total_reviews,
                "avg_tokens_per_review": round(total_tokens / total_reviews)
                if total_reviews
                else 0,
                "per_mode": modes,
            },
        }
    except Exception as exc:
        logger.error("Token stats error: %s", exc)
        return {"success": False, "error": str(exc)}


# ──────────────────────────────────────────────
# API Routes — Settings
# ──────────────────────────────────────────────


@app.get("/api/settings")
async def api_get_settings() -> dict[str, Any]:
    """Returns current settings with API keys masked."""
    config = await aload_config()
    return {"api": get_safe_api_config(), "review": config.get("review", {})}


@app.post("/api/settings")
async def api_save_settings(req: SettingsRequest) -> dict[str, Any]:
    """Saves updated settings.

    Args:
        req: Settings update request.
    """
    try:
        config = await aload_config()

        if req.api:
            current_api = config.get("api", {})
            for key, value in req.api.items():
                # Skip masked values — they haven't changed
                if "•" in value:
                    continue
                current_api[key] = value
            config["api"] = current_api

        if req.review:
            config["review"] = req.review

        await asave_config(config)
        _apply_config_to_env()
        _invalidate_gh_cache()
        _response_cache.clear()  # Invalidate cached status/stats

        return {"success": True, "message": "Settings saved."}
    except Exception as exc:
        logger.error("Failed to save settings: %s", exc)
        return {"success": False, "message": str(exc)}


# ──────────────────────────────────────────────
# API Routes — Prompts
# ──────────────────────────────────────────────


@app.get("/api/prompts")
async def api_get_prompts() -> dict[str, str]:
    """Returns all prompt templates (parallel file reads)."""
    return await aget_all_prompts()


@app.get("/api/prompts/{mode}")
async def api_get_prompt(mode: str) -> dict[str, str]:
    """Returns the prompt template for a specific mode.

    Args:
        mode: Review mode identifier.
    """
    content = await aload_prompt(mode)
    return {"mode": mode, "content": content}


@app.post("/api/prompts")
async def api_save_prompt(req: PromptSaveRequest) -> dict[str, Any]:
    """Saves a prompt template.

    Args:
        req: Prompt save request.
    """
    try:
        await asave_prompt(req.mode, req.content)
        return {"success": True, "message": f"Prompt template '{req.mode}' updated."}
    except Exception as exc:
        logger.error("Failed to save prompt: %s", exc)
        return {"success": False, "message": str(exc)}


# ──────────────────────────────────────────────
# API Routes — Status
# ──────────────────────────────────────────────


@app.get("/api/status")
async def api_status() -> dict[str, Any]:
    """Checks connection status for all configured services (parallel, cached)."""
    cached = _get_cached("status")
    if cached is not None:
        return cached

    config = await aload_config()
    api = config.get("api", {})

    statuses: dict[str, Any] = {
        "gemini": {"configured": bool(api.get("gemini_api_key")), "status": "unknown"},
        "github": {
            "configured": bool(api.get("github_token") and api.get("github_repo")),
            "status": "unknown",
        },
        "jira": {
            "configured": bool(api.get("jira_url") and api.get("jira_api_token")),
            "status": "unknown",
        },
    }

    async def check_gemini() -> dict[str, Any]:
        if not statuses["gemini"]["configured"]:
            return {"configured": False, "status": "unknown"}

        def _check():
            from google import genai

            client = genai.Client(api_key=api["gemini_api_key"])
            next(iter(client.models.list()))

        try:
            await asyncio.to_thread(_check)
            return {"configured": True, "status": "connected"}
        except Exception as exc:
            return {"configured": True, "status": f"error: {exc}"}

    async def check_github() -> dict[str, Any]:
        if not statuses["github"]["configured"]:
            return {"configured": False, "status": "unknown"}

        def _check():
            _get_cached_gh()

        try:
            await asyncio.to_thread(_check)
            return {"configured": True, "status": "connected", "repo": api["github_repo"]}
        except Exception as exc:
            return {"configured": True, "status": f"error: {exc}"}

    async def check_jira() -> dict[str, Any]:
        if not statuses["jira"]["configured"]:
            return {"configured": False, "status": "unknown"}

        def _check():
            from agent.jira_client import JiraClient

            jira = JiraClient()
            jira.client.server_info()

        try:
            await asyncio.to_thread(_check)
            return {"configured": True, "status": "connected", "url": api["jira_url"]}
        except Exception as exc:
            return {"configured": True, "status": f"error: {exc}"}

    gemini_s, github_s, jira_s = await asyncio.gather(check_gemini(), check_github(), check_jira())
    result = {"gemini": gemini_s, "github": github_s, "jira": jira_s}
    _set_cache("status", result)
    return result


# ──────────────────────────────────────────────
# API Routes — Jira
# ──────────────────────────────────────────────


@app.get("/api/jira/ticket/{ticket_key}")
async def api_jira_ticket(ticket_key: str) -> dict[str, Any]:
    """Returns details for a Jira ticket.

    Args:
        ticket_key: Jira ticket key (e.g. ``PROJ-123``).
    """
    try:

        def _fetch():
            from agent.jira_client import JiraClient

            return JiraClient().get_ticket(ticket_key)

        ticket = await asyncio.to_thread(_fetch)
        return {"success": True, "ticket": ticket}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ──────────────────────────────────────────────
# API Routes — GitHub
# ──────────────────────────────────────────────


def _build_pr_card(pr) -> dict:
    """Builds a PR card dictionary from a PyGithub PullRequest object.

    Args:
        pr: PyGithub PullRequest object.

    Returns:
        Dictionary suitable for board column or PR list responses.
    """
    return {
        "number": pr.number,
        "title": pr.title or "",
        "user": pr.user.login if pr.user else "",
        "user_avatar": pr.user.avatar_url if pr.user else "",
        "labels": [label.name for label in pr.labels],
        "head": pr.head.ref if pr.head else "",
        "base": pr.base.ref if pr.base else "",
        "created_at": pr.created_at.strftime("%Y-%m-%d %H:%M") if pr.created_at else "",
        "updated_at": pr.updated_at.strftime("%Y-%m-%d %H:%M") if pr.updated_at else "",
        "url": pr.html_url,
        "additions": pr.additions,
        "deletions": pr.deletions,
        "changed_files": pr.changed_files,
    }


@app.get("/api/github/pulls")
async def api_github_pulls() -> dict[str, Any]:
    """Returns a list of open pull requests."""
    try:

        def _fetch():
            gh = _get_cached_gh()
            return [_build_pr_card(pr) for pr in gh.repo.get_pulls(state="open")]

        pulls = await asyncio.to_thread(_fetch)
        return {"success": True, "pulls": pulls}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@app.get("/api/github/repos")
async def api_github_repos() -> dict[str, Any]:
    """Lists repositories accessible to the authenticated user."""
    try:
        config = await aload_config()
        current = config.get("api", {}).get("github_repo", "")

        def _fetch():
            return _get_cached_gh().list_user_repos()

        repos = await asyncio.to_thread(_fetch)
        return {"success": True, "repos": repos, "current": current}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


class SwitchRepoRequest(BaseModel):
    """Request schema for switching the active repository."""

    repo: str


@app.post("/api/github/switch-repo")
async def api_switch_repo(req: SwitchRepoRequest) -> dict[str, Any]:
    """Switches the active repository.

    Args:
        req: Switch repo request containing the new repo name.
    """
    try:
        config = await aload_config()
        config.setdefault("api", {})["github_repo"] = req.repo
        await asave_config(config)
        _invalidate_gh_cache()
        _response_cache.clear()
        return {"success": True, "message": f"Active repo switched to '{req.repo}'."}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@app.get("/api/github/pr/{pr_number}")
async def api_github_pr_detail(pr_number: int) -> dict[str, Any]:
    """Returns detailed information for a pull request.

    Args:
        pr_number: Pull request number.
    """
    try:

        def _fetch():
            return _get_cached_gh().get_pr_detail(pr_number)

        detail = await asyncio.to_thread(_fetch)
        return {"success": True, "pr": detail}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@app.get("/api/github/pr/{pr_number}/diff")
async def api_github_pr_diff(pr_number: int) -> dict[str, Any]:
    """Returns the unified diff text for a pull request formatted for diff2html.

    Args:
        pr_number: Pull request number.
    """
    try:

        def _fetch():
            gh = _get_cached_gh()
            pull = gh.repo.get_pull(pr_number)
            parts: list[str] = []
            for f in pull.get_files():
                header = f"diff --git a/{f.filename} b/{f.filename}\n--- a/{f.filename}\n+++ b/{f.filename}"
                parts.append(f"{header}\n{f.patch or '(binary or empty)'}")
            return "\n\n".join(parts)

        diff = await asyncio.to_thread(_fetch)
        return {"success": True, "diff": diff}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ──────────────────────────────────────────────
# API Routes — Board (Kanban)
# ──────────────────────────────────────────────


@app.get("/api/board/columns")
async def api_board_columns() -> dict[str, Any]:
    """Returns PRs grouped into Kanban board columns (parallel fetches, N+1 fixed)."""
    try:
        gh = await asyncio.to_thread(_get_cached_gh)
        repo = gh.repo

        # Fetch open and closed PRs in parallel
        async def fetch_open():
            return await asyncio.to_thread(lambda: list(repo.get_pulls(state="open")))

        async def fetch_closed():
            return await asyncio.to_thread(
                lambda: list(
                    itertools.islice(
                        repo.get_pulls(state="closed", sort="updated", direction="desc"),
                        BOARD_CLOSED_PR_LIMIT,
                    )
                )
            )

        open_prs, closed_prs = await asyncio.gather(fetch_open(), fetch_closed())

        # Fetch all reviews in parallel — solves N+1
        async def fetch_reviews(pr):
            reviews = await asyncio.to_thread(lambda: list(pr.get_reviews()))
            return pr, reviews

        review_results = await asyncio.gather(*[fetch_reviews(pr) for pr in open_prs])

        columns: dict[str, list] = {"open": [], "in_review": [], "merged": [], "closed": []}

        for pr, reviews in review_results:
            card = _build_pr_card(pr)
            has_review = any(
                r.state in ("APPROVED", "CHANGES_REQUESTED", "COMMENTED") for r in reviews
            )
            if has_review:
                card["review_state"] = reviews[-1].state if reviews else ""
                columns["in_review"].append(card)
            else:
                columns["open"].append(card)

        for pr in closed_prs:
            card = _build_pr_card(pr)
            if pr.merged:
                card["merged_at"] = pr.merged_at.strftime("%Y-%m-%d %H:%M") if pr.merged_at else ""
                columns["merged"].append(card)
            else:
                columns["closed"].append(card)

        return {"success": True, "columns": columns}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ──────────────────────────────────────────────
# API Routes — Stats
# ──────────────────────────────────────────────


@app.get("/api/stats")
async def api_stats() -> dict[str, Any]:
    """Returns repository and review statistics (parallel fetches, cached)."""
    cached = _get_cached("stats")
    if cached is not None:
        return cached

    try:
        config = await aload_config()
        repo_name = config.get("api", {}).get("github_repo", "")

        gh = await asyncio.to_thread(_get_cached_gh)
        repo = gh.repo

        async def get_open_count():
            return await asyncio.to_thread(lambda: repo.get_pulls(state="open").totalCount)

        async def get_closed_count():
            return await asyncio.to_thread(lambda: repo.get_pulls(state="closed").totalCount)

        async def get_contributors():
            def _fetch():
                result: list[dict] = []
                try:
                    for c in repo.get_contributors():
                        result.append(
                            {
                                "login": c.login,
                                "avatar": c.avatar_url,
                                "contributions": c.contributions,
                            }
                        )
                        if len(result) >= TOP_CONTRIBUTORS_LIMIT:
                            break
                except Exception:
                    pass
                return result

            return await asyncio.to_thread(_fetch)

        open_prs, closed_prs, contributors = await asyncio.gather(
            get_open_count(), get_closed_count(), get_contributors()
        )

        result = {
            "success": True,
            "stats": {
                "open_prs": open_prs,
                "closed_prs": closed_prs,
                "total_reviews": len(review_history),
                "top_contributors": contributors,
                "repo_name": repo_name,
            },
        }
        _set_cache("stats", result)
        return result
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ──────────────────────────────────────────────
# Helper
# ──────────────────────────────────────────────


def _sync_dotenv_to_config() -> None:
    """Merges .env values into config.json at startup.

    If the .env file contains API keys that differ from the stored config,
    they are written into config.json so that _apply_config_to_env() picks
    them up. This ensures that editing .env is always reflected on the next
    restart without having to re-save from the Settings panel.
    """
    import os

    from agent.config_manager import load_config, save_config

    env_to_config: dict[str, str] = {
        "GEMINI_API_KEY": "gemini_api_key",
        "GITHUB_TOKEN": "github_token",
        "GITHUB_REPO": "github_repo",
        "JIRA_URL": "jira_url",
        "JIRA_EMAIL": "jira_email",
        "JIRA_API_TOKEN": "jira_api_token",
        "JIRA_REVIEW_STATUS": "jira_review_status",
        "JIRA_AC_FIELD": "jira_ac_field",
    }

    config = load_config()
    api = config.setdefault("api", {})
    changed = False

    for env_key, config_key in env_to_config.items():
        env_val = os.getenv(env_key, "")
        if env_val and env_val != api.get(config_key, ""):
            api[config_key] = env_val
            changed = True
            logger.info("Startup: synced %s from .env into config.", config_key)

    if changed:
        save_config(config)


def _apply_config_to_env() -> None:
    """Writes API settings from config to environment variables.

    This ensures that client modules (which use os.getenv) pick up
    changes made through the web panel without requiring a restart.
    """
    import os

    api = get_api_config()
    env_map = {
        "gemini_api_key": "GEMINI_API_KEY",
        "github_token": "GITHUB_TOKEN",
        "github_repo": "GITHUB_REPO",
        "jira_url": "JIRA_URL",
        "jira_email": "JIRA_EMAIL",
        "jira_api_token": "JIRA_API_TOKEN",
        "jira_review_status": "JIRA_REVIEW_STATUS",
        "jira_ac_field": "JIRA_AC_FIELD",
    }
    for config_key, env_key in env_map.items():
        value = api.get(config_key, "")
        if value:
            os.environ[env_key] = value
