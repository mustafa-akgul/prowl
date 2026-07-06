"""Worker — Celery task definitions for background review processing.

Defines asynchronous tasks that can be dispatched via the message broker.
Tasks include review execution and webhook event processing.
"""

from __future__ import annotations

import logging

from dotenv import load_dotenv

load_dotenv()


from agent.celery_app import celery_app
from agent.history_manager import save_review

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="agent.worker.sub_agent_task", max_retries=3)
def sub_agent_task(
    self,
    mode: str,
    diff: str,
    jira_context: str,
    enriched_instructions: str,
    corporate_context: str,
) -> dict:
    from agent.review_agent import _build_llm_client

    try:
        llm = _build_llm_client()
        review_text = llm.review(
            diff=diff,
            jira_context=jira_context,
            pm_instructions=enriched_instructions,
            mode=mode,
            corporate_context=corporate_context,
        )
        return {"mode": mode, "review": review_text, "usage_stats": llm.usage_stats}
    except Exception as exc:
        logger.error(f"Sub-agent {mode} failed: {exc}")
        return {"mode": mode, "error": str(exc)}


@celery_app.task(bind=True, name="agent.worker.lead_agent_task", max_retries=2)
def lead_agent_task(
    self,
    results: list[dict],
    pr_number: int,
    jira_key: str | None,
    file_count: int,
    additions: int,
    deletions: int,
) -> dict:
    from agent.github_client import GitHubClient
    from agent.jira_client import JiraClient
    from agent.review_agent import _build_llm_client, _publish_results

    logger.info("Lead agent summarizing results for PR #%d", pr_number)

    reviews = []
    total_prompt = 0
    total_completion = 0
    for r in results:
        if "usage_stats" in r:
            total_prompt += r["usage_stats"].get("prompt_tokens", 0)
            total_completion += r["usage_stats"].get("completion_tokens", 0)

        if "review" in r:
            reviews.append(f"### {r['mode'].capitalize()} Analysis\n{r['review']}")
        else:
            reviews.append(f"### {r['mode'].capitalize()} Analysis\nFailed: {r.get('error')}")

    combined_reviews = "\n\n".join(reviews)

    lead_prompt = (
        "You are the Lead Code Review Agent. Consolidate and summarize the following specialized reviews "
        "into a single cohesive, well-structured, non-contradictory final code review. "
        "Eliminate duplicate suggestions and provide a clear executive summary at the top.\n\n"
        f"{combined_reviews}"
    )

    try:
        llm = _build_llm_client()
        final_review = llm._call_api(lead_prompt)
        total_prompt += llm.usage_stats.get("prompt_tokens", 0)
        total_completion += llm.usage_stats.get("completion_tokens", 0)
    except Exception as exc:
        logger.error("Lead agent LLM failure: %s", exc)
        final_review = f"Lead Agent failed to combine reviews. Raw reviews:\n\n{combined_reviews}"

    gh = GitHubClient()
    jira_client = JiraClient() if jira_key else None

    _publish_results(gh, jira_client, pr_number, jira_key, final_review, "multi-agent")

    try:
        from agent.notifier import send_review_notification
        from agent.report_generator import ReportGenerator

        rg = ReportGenerator()
        html_path = rg.generate_html(
            pr_number, "multi-agent", final_review, jira_key or "", file_count, additions, deletions
        )
        pdf_path = rg.generate_pdf(
            pr_number, "multi-agent", final_review, jira_key or "", file_count, additions, deletions
        )
        pr_url = f"https://github.com/{gh.repo.full_name}/pull/{pr_number}"
        send_review_notification(
            pr_number, "multi-agent", final_review, pr_url, html_path, pdf_path
        )
    except Exception as e:
        logger.warning(f"Reports failed: {e}")

    record = save_review(
        pr_number,
        "multi-agent",
        final_review,
        usage_stats={"prompt_tokens": total_prompt, "completion_tokens": total_completion},
    )
    return {
        "success": True,
        "pr_number": pr_number,
        "mode": "multi-agent",
        "review": final_review,
        "timestamp": record.get("timestamp", ""),
        "usage_stats": {"prompt_tokens": total_prompt, "completion_tokens": total_completion},
    }


@celery_app.task(
    bind=True,
    name="agent.worker.run_review_task",
    max_retries=2,
    default_retry_delay=30,
)
def run_review_task(
    self,
    pr_number: int,
    mode: str = "base",
    pm_instructions: str | None = None,
) -> dict:
    """Runs a code review as a background Celery task.

    This is the distributed alternative to the in-process background
    task used by the dashboard. Results are stored both in the Celery
    result backend and in the history file.

    Args:
        self: Celery task instance (bound).
        pr_number: Pull request number.
        mode: Review mode.
        pm_instructions: Optional PM instructions.

    Returns:
        Dictionary with ``success``, ``pr_number``, ``mode``, and
        ``review`` or ``error`` keys.
    """
    logger.info(
        "Celery task started — PR #%d, mode: %s",
        pr_number,
        mode,
    )

    try:
        from agent.review_agent import run as run_review
        from agent.review_agent import run_multi_agent

        if mode == "multi-agent":
            run_multi_agent(
                pr_number=pr_number,
                pm_instructions=pm_instructions,
            )
            return {
                "success": True,
                "pr_number": pr_number,
                "mode": mode,
                "action": "chord_triggered",
            }

        result = run_review(
            pr_number=pr_number,
            mode=mode,
            pm_instructions=pm_instructions,
        )

        if result is None:
            return {
                "success": False,
                "pr_number": pr_number,
                "mode": mode,
                "error": "Review could not be completed. Check the logs.",
            }

        review_text, usage = result
        record = save_review(pr_number, mode, review_text, usage_stats=usage)

        logger.info("Celery task completed — PR #%d", pr_number)
        return {
            "success": True,
            "pr_number": pr_number,
            "mode": mode,
            "review": review_text,
            "usage_stats": usage,
            "timestamp": record.get("timestamp", ""),
        }

    except Exception as exc:
        logger.error("Celery task failed — PR #%d: %s", pr_number, exc)
        raise self.retry(exc=exc)


@celery_app.task(
    bind=True,
    name="agent.worker.process_webhook_event",
    max_retries=3,
    default_retry_delay=10,
)
def process_webhook_event(
    self,
    event_type: str,
    payload: dict,
) -> dict:
    """Processes a webhook event as a background task.

    Dispatches to the appropriate handler based on event type.

    Args:
        self: Celery task instance (bound).
        event_type: Webhook event type (e.g. ``pr_opened``,
            ``pr_synchronize``, ``comment_created``).
        payload: Webhook payload dictionary.

    Returns:
        Processing result dictionary.
    """
    logger.info("Processing webhook event: %s", event_type)

    try:
        if event_type in ("pr_opened", "pr_synchronize"):
            pr_number = payload.get("pr_number")
            if not pr_number:
                return {"success": False, "error": "Missing pr_number in payload"}

            task = run_review_task.delay(
                pr_number=pr_number,
                mode=payload.get("mode", "base"),
                pm_instructions=payload.get("instructions"),
            )
            return {
                "success": True,
                "event": event_type,
                "task_id": task.id,
            }

        elif event_type == "comment_created":
            pr_number = payload.get("pr_number")
            comment_body = payload.get("comment_body", "")

            if "@review-agent" not in comment_body:
                return {"success": True, "event": event_type, "action": "ignored"}

            # Check for chat/reply mode
            from agent.review_agent import run_chat

            chat_keywords = ("reply", "chat", "ask")
            if any(kw in comment_body.lower() for kw in chat_keywords):
                res = run_chat(pr_number, comment_body)
                return {
                    "success": True,
                    "event": event_type,
                    "chat": True,
                    "reply": res.get("reply", ""),
                }

            # Parse mode from comment
            mode = "base"
            for m in ("security", "performance", "base"):
                if f"mode: {m}" in comment_body.lower() or f"mode:{m}" in comment_body.lower():
                    mode = m
                    break

            # Extract instructions (everything after mode and @review-agent)
            import re

            instructions = (
                re.sub(
                    r"@review-agent|mode:\s*(security|performance|base)",
                    "",
                    comment_body,
                    flags=re.IGNORECASE,
                ).strip()
                or None
            )

            task = run_review_task.delay(
                pr_number=pr_number,
                mode=mode,
                pm_instructions=instructions,
            )
            return {
                "success": True,
                "event": event_type,
                "task_id": task.id,
                "mode": mode,
            }

        elif event_type == "jira_status_change":
            # Jira ticket moved to "In Review" — trigger review for linked PR
            pr_number = payload.get("pr_number")
            if pr_number:
                task = run_review_task.delay(pr_number=pr_number, mode="base")
                return {"success": True, "event": event_type, "task_id": task.id}
            return {"success": True, "event": event_type, "action": "no_linked_pr"}

        else:
            logger.warning("Unknown event type: %s", event_type)
            return {"success": False, "error": f"Unknown event type: {event_type}"}

    except Exception as exc:
        logger.error("Webhook processing failed: %s", exc)
        raise self.retry(exc=exc)
