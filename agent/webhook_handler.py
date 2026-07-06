"""Webhook handler — receives and validates GitHub/Jira webhook events.

Provides FastAPI endpoints for GitHub Webhooks (PR opened, synchronize,
comment created) and Jira Webhooks (status change). Events are validated
using HMAC signatures and dispatched to Celery workers for processing.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


def _verify_github_signature(
    payload: bytes,
    signature: str | None,
    secret: str | None = None,
) -> bool:
    """Verifies the GitHub webhook HMAC-SHA256 signature.

    Args:
        payload: Raw request body bytes.
        signature: X-Hub-Signature-256 header value.
        secret: Webhook secret. Read from GITHUB_WEBHOOK_SECRET env var
            if not provided.

    Returns:
        True if the signature is valid or no secret is configured.
    """
    if secret is None:
        secret = os.getenv("GITHUB_WEBHOOK_SECRET", "")

    if not secret:
        logger.warning("GITHUB_WEBHOOK_SECRET not set — skipping signature verification.")
        return True

    if not signature:
        return False

    expected = (
        "sha256="
        + hmac.new(
            secret.encode("utf-8"),
            payload,
            hashlib.sha256,
        ).hexdigest()
    )

    return hmac.compare_digest(expected, signature)


@router.post("/github")
async def github_webhook(
    request: Request,
    x_github_event: str = Header(None, alias="X-GitHub-Event"),
    x_hub_signature_256: str = Header(None, alias="X-Hub-Signature-256"),
) -> dict[str, Any]:
    """Receives and processes GitHub webhook events.

    Supported events:
    - ``pull_request`` (opened, synchronize) → triggers automatic review
    - ``issue_comment`` (created) → checks for @review-agent mention

    Args:
        request: FastAPI request object.
        x_github_event: GitHub event type header.
        x_hub_signature_256: HMAC signature header.

    Returns:
        Processing result dictionary.

    Raises:
        HTTPException: If the signature is invalid (403) or the event
            type is not supported (400).
    """
    body = await request.body()

    if not _verify_github_signature(body, x_hub_signature_256):
        raise HTTPException(status_code=403, detail="Invalid webhook signature.")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.")

    if x_github_event == "pull_request":
        action = payload.get("action", "")
        if action in ("opened", "synchronize"):
            pr = payload.get("pull_request", {})
            pr_number = pr.get("number")
            if not pr_number:
                return {"success": False, "error": "Missing PR number"}

            logger.info(
                "GitHub webhook: PR #%d %s — dispatching review.",
                pr_number,
                action,
            )

            try:
                from agent.worker import process_webhook_event

                task = process_webhook_event.delay(
                    event_type=f"pr_{action}",
                    payload={"pr_number": pr_number},
                )
                return {"success": True, "task_id": task.id}
            except Exception as exc:
                # Fallback: run in-process if Celery is not available
                logger.warning("Celery not available, running in-process: %s", exc)
                return await _run_review_inprocess(pr_number, "base")

        return {"success": True, "action": "ignored", "pr_action": action}

    elif x_github_event in ("issue_comment", "pull_request_review_comment"):
        action = payload.get("action", "")
        comment = payload.get("comment", {})
        comment_body = comment.get("body", "")

        # PR number can be in issue or in pull_request directly
        pr_number = None
        if x_github_event == "issue_comment":
            issue = payload.get("issue", {})
            if issue.get("pull_request"):
                pr_number = issue.get("number")
        else:  # pull_request_review_comment
            pr = payload.get("pull_request", {})
            pr_number = pr.get("number")

        # Only process PR comments mentioning @review-agent
        if action == "created" and pr_number and "@review-agent" in comment_body:
            logger.info(
                "GitHub webhook: @review-agent mentioned in PR #%d comment.",
                pr_number,
            )

            try:
                from agent.worker import process_webhook_event

                task = process_webhook_event.delay(
                    event_type="comment_created",
                    payload={
                        "pr_number": pr_number,
                        "comment_body": comment_body,
                        "comment_id": comment.get("id"),
                    },
                )
                return {"success": True, "task_id": task.id}
            except Exception as exc:
                logger.warning("Celery not available, running in-process: %s", exc)
                return await _run_review_inprocess(pr_number, "base")

        return {"success": True, "action": "ignored"}

    elif x_github_event == "ping":
        return {"success": True, "message": "pong"}

    return {"success": True, "action": "ignored", "event": x_github_event}


@router.post("/jira")
async def jira_webhook(request: Request) -> dict[str, Any]:
    """Receives and processes Jira webhook events.

    Triggers a review when a ticket is moved to "In Review" status
    and has a linked PR.

    Args:
        request: FastAPI request object.

    Returns:
        Processing result dictionary.
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.")

    webhook_event = payload.get("webhookEvent", "")
    issue = payload.get("issue", {})
    changelog = payload.get("changelog", {})

    # Check for status change to "In Review"
    if webhook_event == "jira:issue_updated":
        status_change = None
        for item in changelog.get("items", []):
            if item.get("field") == "status":
                status_change = item.get("toString", "")
                break

        review_status = os.getenv("JIRA_REVIEW_STATUS", "In Review")
        if status_change and status_change.lower() == review_status.lower():
            jira_key = issue.get("key", "")
            logger.info(
                "Jira webhook: %s moved to '%s' — looking for linked PR.",
                jira_key,
                review_status,
            )

            # Try to find a PR linked to this ticket via GitHub
            pr_number = await _find_pr_for_jira_key(jira_key)
            if pr_number:
                try:
                    from agent.worker import process_webhook_event

                    task = process_webhook_event.delay(
                        event_type="jira_status_change",
                        payload={"pr_number": pr_number, "jira_key": jira_key},
                    )
                    return {"success": True, "task_id": task.id}
                except Exception as exc:
                    logger.warning("Celery not available: %s", exc)
                    return await _run_review_inprocess(pr_number, "base")

            return {"success": True, "action": "no_linked_pr", "jira_key": jira_key}

    return {"success": True, "action": "ignored"}


async def _find_pr_for_jira_key(jira_key: str) -> int | None:
    """Searches open PRs for one that references the given Jira key.

    Args:
        jira_key: Jira ticket key to search for.

    Returns:
        PR number if found, None otherwise.
    """
    import asyncio

    try:
        from agent.github_client import GitHubClient

        def _search():
            gh = GitHubClient()
            for pr in gh.repo.get_pulls(state="open"):
                title = pr.title or ""
                body = pr.body or ""
                if jira_key in title or jira_key in body:
                    return pr.number
            return None

        return await asyncio.to_thread(_search)
    except Exception as exc:
        logger.error("Failed to search PRs for Jira key %s: %s", jira_key, exc)
        return None


async def _run_review_inprocess(pr_number: int, mode: str) -> dict[str, Any]:
    """Fallback: runs a review in-process when Celery is not available.

    Args:
        pr_number: Pull request number.
        mode: Review mode.

    Returns:
        Result dictionary.
    """
    import asyncio

    try:
        from agent.history_manager import save_review
        from agent.review_agent import run as run_review

        result = await asyncio.to_thread(run_review, pr_number=pr_number, mode=mode)
        if result:
            review_text, usage = result
            save_review(pr_number, mode, review_text, usage_stats=usage)
            return {
                "success": True,
                "pr_number": pr_number,
                "processed": "inprocess",
                "usage": usage,
            }
        return {"success": False, "error": "Review failed"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
