"""Review Agent — main orchestration module.

Manages the full review pipeline given a PR number:
1. Extract a Jira key from the PR description
2. Fetch Jira ticket context
3. Fetch the PR diff
4. Generate a review with the configured LLM provider (Ollama or Gemini)
5. Post the result as a PR comment and update the Jira ticket

Can also be run directly from the CLI:
    python -m agent.review_agent --pr 42 --mode security
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Literal

from agent.base_client import BaseLLMClient, LLMError
from agent.github_client import GitHubClient, GitHubError
from agent.jira_client import JiraClient, JiraError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

# Warning posted to the PR when no Jira key is found
NO_JIRA_WARNING = (
    "⚠️ **No Jira ticket found** — only code quality was reviewed. "
    "Consider adding a Jira key (e.g. `PROJ-123`) to the PR title or description."
)

# Comment posted to the PR when an unrecoverable error occurs
API_ERROR_COMMENT = (
    "❌ **Review Agent error** — an error occurred during automated code review. "
    "Please check the logs.\n\n"
    "```\n{error}\n```"
)


def _resolve_jira_key(gh: GitHubClient, pr_number: int) -> str | None:
    """Extracts a Jira key from the PR title or body.

    Args:
        gh: Authenticated GitHub client.
        pr_number: Pull request number.

    Returns:
        Jira key string (e.g. ``PROJ-42``) or ``None`` if not found.

    Raises:
        GitHubError: If the PR description cannot be retrieved.
    """
    pr_desc = gh.get_pr_description(pr_number)
    jira_key = GitHubClient.extract_jira_key(pr_desc["title"])
    if jira_key is None:
        jira_key = GitHubClient.extract_jira_key(pr_desc["body"])
    return jira_key


def _get_jira_context(jira_key: str) -> tuple[str, JiraClient | None]:
    """Fetches full Jira ticket context including sub-tasks and comments.

    Args:
        jira_key: Jira ticket key (e.g. ``PROJ-42``).

    Returns:
        A tuple of (formatted context string, JiraClient instance).
        If the ticket cannot be read, returns (empty string, None).
    """
    try:
        jira = JiraClient()
        context = jira.get_ticket_full_context(jira_key)
        return context, jira
    except JiraError as exc:
        logger.warning("Could not read Jira ticket: %s", exc)
        return "", None


def _format_developer_context(pr_context: dict) -> str:
    """Formats review comments and commits into developer context string.

    Provides the LLM with the history of developer discussions and
    commit progression for a more informed review.

    Args:
        pr_context: Dictionary from ``GitHubClient.get_pull_full_context()``.

    Returns:
        Formatted developer context string.
    """
    parts: list[str] = []

    review_comments = pr_context.get("review_comments", [])
    if review_comments:
        parts.append("**Previous Inline Review Comments:**")
        for rc in review_comments:
            parts.append(
                f"  - **{rc['user']}** on `{rc['path']}` (line {rc['line']}): {rc['body'][:200]}"
            )

    issue_comments = pr_context.get("issue_comments", [])
    if issue_comments:
        parts.append("\n**Previous PR Comments (Thread History):**")
        for ic in issue_comments:
            parts.append(f"  - **{ic['user']}**: {ic['body'][:300]}")

    commits = pr_context.get("commits", [])
    if commits:
        parts.append("\n**Commit History:**")
        for c in commits:
            msg_first_line = c["message"].split("\n")[0][:100]
            parts.append(f"  - `{c['sha']}` {c['author']}: {msg_first_line}")

    return "\n".join(parts) if parts else ""


def _publish_results(
    gh: GitHubClient,
    jira: JiraClient | None,
    pr_number: int,
    jira_key: str | None,
    review_text: str,
    mode: str,
) -> None:
    """Posts the review as a PR comment and updates the Jira ticket.

    Args:
        gh: Authenticated GitHub client.
        jira: Authenticated Jira client, or ``None`` if unavailable.
        pr_number: Pull request number.
        jira_key: Jira ticket key, or ``None`` if not found.
        review_text: Generated review text.
        mode: Review mode used.
    """
    comment_body = f"## 🤖 AI Code Review ({mode})\n\n"
    if not jira_key:
        comment_body += f"{NO_JIRA_WARNING}\n\n---\n\n"
    comment_body += review_text
    _safe_post_comment(gh, pr_number, comment_body)

    if jira_key and jira:
        try:
            jira.add_comment(
                jira_key,
                f"🤖 *AI Code Review completed (PR #{pr_number}, mode: {mode})*\n\n{review_text}",
            )
            jira.transition_status(jira_key)
        except JiraError as exc:
            logger.warning("Jira update failed: %s", exc)


def _build_llm_client() -> BaseLLMClient:
    """Instantiates the LLM client for the configured provider.

    Reads ``provider`` from the review config. Supported values:
    - ``"ollama"`` — local Ollama instance (no API key required)
    - ``"gemini"`` — Google Gemini API (requires ``GEMINI_API_KEY``)

    Returns:
        An instantiated LLM client ready for use.
    """
    from agent.config_manager import get_review_config

    provider = get_review_config().get("provider", "gemini")
    if provider == "ollama":
        from agent.ollama_client import OllamaClient

        return OllamaClient()
    from agent.gemini_client import GeminiClient

    return GeminiClient()


def run(
    pr_number: int,
    mode: Literal["base", "security", "performance"] = "base",
    pm_instructions: str | None = None,
) -> tuple[str, dict] | None:
    """Orchestrates the full review pipeline.

    Args:
        pr_number: Pull request number.
        mode: Review mode (``base``, ``security``, ``performance``).
        pm_instructions: Optional extra instructions from the PM.

    Returns:
        Generated review text and usage_stats, or ``None`` on failure.
    """
    # --- GitHub client ---
    try:
        gh = GitHubClient()
    except GitHubError as exc:
        logger.error("GitHub connection error: %s", exc)
        return None

    # --- Fetch PR context in a single API call (title + body + diff) ---
    try:
        pr_context = gh.get_pull_full_context(pr_number)
    except GitHubError as exc:
        logger.error("Failed to retrieve PR context: %s", exc)
        _safe_post_comment(gh, pr_number, API_ERROR_COMMENT.format(error=str(exc)))
        return None

    # --- Extract Jira key ---
    jira_key = GitHubClient.extract_jira_key(pr_context["title"])
    if jira_key is None:
        jira_key = GitHubClient.extract_jira_key(pr_context["body"])

    jira_context: str = ""
    jira_client: JiraClient | None = None

    # --- Fetch Jira context ---
    if jira_key:
        jira_context, jira_client = _get_jira_context(jira_key)
        if not jira_context:
            jira_key = None  # Skip Jira update steps if ticket is unreachable
    else:
        logger.info("PR #%d — no Jira key found.", pr_number)

    diff = pr_context["diff"]

    # --- Build developer context from review comments and commits ---
    dev_context = _format_developer_context(pr_context)
    enriched_instructions = pm_instructions or ""
    if dev_context:
        enriched_instructions = (
            f"{enriched_instructions}\n\n{dev_context}" if enriched_instructions else dev_context
        )

    ast_context = ""
    pre_llm_findings = ""
    try:
        from agent.ast_analyzer import analyze_diff_context, run_linter_on_diff

        # Fetch PR detail to locate changed files for AST parsing
        pr_detail = gh.get_pr_detail(pr_number)
        head_sha = pr_detail.get("head")

        file_to_source = {}
        for f in pr_detail.get("files", []):
            filename = f["filename"]
            if filename.endswith(".py") and f["status"] != "removed":
                try:
                    file_to_source[filename] = gh.get_file_content(filename, head_sha)
                except Exception:
                    pass

        if file_to_source:
            pre_llm_findings = run_linter_on_diff(diff, file_to_source)
            ast_context = analyze_diff_context(diff, file_to_source)

    except ImportError:
        ast_context = ""

    if ast_context:
        enriched_instructions += f"\n\n{ast_context}"

    if pre_llm_findings:
        # Pre-LLM rules failed! We can bypass LLM and return immediately to save API costs.
        logger.warning(f"AST Linter found critical issues. Bypassing LLM for PR #{pr_number}.")
        final_review = f"{pre_llm_findings}\n\n⚠️ **LLM Review Skipped**: Critical static analysis failures were detected. Please fix the issues above before requesting a full AI review."
        _publish_results(gh, jira_client, pr_number, jira_key, final_review, "linter")

        try:
            from agent.notifier import send_review_notification
            from agent.report_generator import ReportGenerator

            file_count = diff.count("--- a/")
            additions = diff.count("\n+") - diff.count("\n+++")
            deletions = diff.count("\n-") - diff.count("\n---")

            rg = ReportGenerator()
            html_path = rg.generate_html(
                pr_number, "linter", final_review, jira_key or "", file_count, additions, deletions
            )
            pdf_path = rg.generate_pdf(
                pr_number, "linter", final_review, jira_key or "", file_count, additions, deletions
            )
            pr_url = f"https://github.com/{gh.repo.full_name}/pull/{pr_number}"

            send_review_notification(
                pr_number=pr_number,
                mode="linter",
                review_text=final_review,
                pr_url=pr_url,
                html_report_path=html_path,
                pdf_report_path=pdf_path,
            )
        except Exception as exc:
            logger.warning("Reports failed: %s", exc)

        return final_review, {"prompt_tokens": 0, "completion_tokens": 0}

    # --- RAG Corporate Knowledge (Stage 2) ---
    corporate_context = ""
    try:
        import os

        from agent.rag.context_retriever import RAGContextRetriever
        from agent.rag.embedding_provider import create_embedding_provider
        from agent.rag.vector_store import VectorStore

        provider_type = os.getenv("RAG_EMBEDDING_PROVIDER", "default")
        model_name = os.getenv("RAG_MODEL_NAME", "all-MiniLM-L6-v2")

        # Fallback to default safely if sentence-transformers not installed
        try:
            emb_provider = create_embedding_provider(provider_type, model_name)
            store = VectorStore(embedding_provider=emb_provider)
            retriever = RAGContextRetriever(vector_store=store)

            # Query using PR title, body, and start of diff
            query_text = f"{pr_context['title']} {pr_context['body']} {diff[:500]}"
            corporate_context = retriever.get_context(query=query_text)
        except ImportError as e:
            logger.warning("RAG disabled: %s", e)
    except Exception as exc:
        logger.warning("Failed to retrieve corporate context: %s", exc)

    # --- Generate review with the configured LLM provider ---
    try:
        llm = _build_llm_client()
        review_text = llm.review(
            diff=diff,
            jira_context=jira_context,
            pm_instructions=enriched_instructions,
            mode=mode,
            corporate_context=corporate_context,
        )
        usage_stats = llm.usage_stats
    except LLMError as exc:
        logger.error("LLM review error: %s", exc)
        _safe_post_comment(gh, pr_number, API_ERROR_COMMENT.format(error=str(exc)))
        return None

    # --- Publish results ---
    _publish_results(gh, jira_client, pr_number, jira_key, review_text, mode)

    # --- Generate Reports and Notifications (Stage 4) ---
    try:
        from agent.notifier import send_review_notification
        from agent.report_generator import ReportGenerator

        file_count = diff.count("--- a/")
        additions = diff.count("\n+") - diff.count("\n+++")
        deletions = diff.count("\n-") - diff.count("\n---")

        rg = ReportGenerator()
        html_path = rg.generate_html(
            pr_number, mode, review_text, jira_key or "", file_count, additions, deletions
        )
        pdf_path = rg.generate_pdf(
            pr_number,
            mode,
            review_text,
            jira_key=jira_key or "",
            file_count=file_count,
            additions=additions,
            deletions=deletions,
        )

        pr_url = f"https://github.com/{gh.repo.full_name}/pull/{pr_number}"

        send_review_notification(
            pr_number=pr_number,
            mode=mode,
            review_text=review_text,
            pr_url=pr_url,
            html_report_path=html_path,
            pdf_report_path=pdf_path,
        )
    except Exception as exc:
        logger.warning("Failed to generate report or send notifications: %s", exc)

    logger.info(
        "Review complete — PR #%d, mode: %s, Jira: %s",
        pr_number,
        mode,
        jira_key or "none",
    )
    return review_text, usage_stats


def run_multi_agent(
    pr_number: int,
    pm_instructions: str | None = None,
) -> None:
    """Orchestrates the multi-agent review pipeline via Celery chords.

    Args:
        pr_number: Pull request number.
        pm_instructions: Optional extra instructions from the PM.
    """
    try:
        gh = GitHubClient()
    except GitHubError as exc:
        logger.error("GitHub connection error: %s", exc)
        return

    try:
        pr_context = gh.get_pull_full_context(pr_number)
    except GitHubError as exc:
        logger.error("Failed to retrieve PR context: %s", exc)
        _safe_post_comment(gh, pr_number, API_ERROR_COMMENT.format(error=str(exc)))
        return

    jira_key = GitHubClient.extract_jira_key(pr_context["title"])
    if jira_key is None:
        jira_key = GitHubClient.extract_jira_key(pr_context["body"])

    jira_context: str = ""
    jira_client: JiraClient | None = None

    if jira_key:
        jira_context, jira_client = _get_jira_context(jira_key)
        if not jira_context:
            jira_key = None
    else:
        logger.info("PR #%d — no Jira key found.", pr_number)

    diff = pr_context["diff"]
    dev_context = _format_developer_context(pr_context)
    enriched_instructions = pm_instructions or ""
    if dev_context:
        enriched_instructions = (
            f"{enriched_instructions}\n\n{dev_context}" if enriched_instructions else dev_context
        )

    ast_context = ""
    pre_llm_findings = ""
    try:
        from agent.ast_analyzer import analyze_diff_context, run_linter_on_diff

        pr_detail = gh.get_pr_detail(pr_number)
        head_sha = pr_detail.get("head")

        file_to_source = {}
        for f in pr_detail.get("files", []):
            filename = f["filename"]
            if filename.endswith(".py") and f["status"] != "removed":
                try:
                    file_to_source[filename] = gh.get_file_content(filename, head_sha)
                except Exception:
                    pass

        if file_to_source:
            pre_llm_findings = run_linter_on_diff(diff, file_to_source)
            ast_context = analyze_diff_context(diff, file_to_source)

    except ImportError:
        ast_context = ""

    if ast_context:
        enriched_instructions += f"\n\n{ast_context}"

    if pre_llm_findings:
        logger.warning(
            f"AST Linter found critical issues. Bypassing Multi-Agent LLM for PR #{pr_number}."
        )
        final_review = f"{pre_llm_findings}\n\n⚠️ **LLM Review Skipped**: Critical static analysis failures were detected. Please fix the issues above before requesting a full AI review."
        _publish_results(gh, jira_client, pr_number, jira_key, final_review, "multi-agent-linter")
        return

    corporate_context = ""
    try:
        import os

        from agent.rag.context_retriever import RAGContextRetriever
        from agent.rag.embedding_provider import create_embedding_provider
        from agent.rag.vector_store import VectorStore

        provider_type = os.getenv("RAG_EMBEDDING_PROVIDER", "default")
        model_name = os.getenv("RAG_MODEL_NAME", "all-MiniLM-L6-v2")

        try:
            emb_provider = create_embedding_provider(provider_type, model_name)
            store = VectorStore(embedding_provider=emb_provider)
            retriever = RAGContextRetriever(vector_store=store)
            query_text = f"{pr_context['title']} {pr_context['body']} {diff[:500]}"
            corporate_context = retriever.get_context(query=query_text)
        except ImportError as e:
            logger.warning("RAG disabled: %s", e)
    except Exception as exc:
        logger.warning("Failed to retrieve corporate context: %s", exc)

    file_count = diff.count("--- a/")
    additions = diff.count("\n+") - diff.count("\n+++")
    deletions = diff.count("\n-") - diff.count("\n---")

    try:
        from celery import chord

        from agent.worker import lead_agent_task, sub_agent_task

        modes = ["security", "performance", "style"]
        logger.info("Triggering multi-agent tasks for PR #%d...", pr_number)

        header = [
            sub_agent_task.s(
                mode=m,
                diff=diff,
                jira_context=jira_context,
                enriched_instructions=enriched_instructions,
                corporate_context=corporate_context,
            )
            for m in modes
        ]

        callback = lead_agent_task.s(
            pr_number=pr_number,
            jira_key=jira_key,
            file_count=file_count,
            additions=additions,
            deletions=deletions,
        )

        chord(header)(callback)
        logger.info("Multi-agent chord triggered successfully.")
    except Exception as exc:
        logger.error("Failed to orchestrate multi-agent: %s", exc)
        _safe_post_comment(
            gh, pr_number, API_ERROR_COMMENT.format(error=f"Multi-Agent setup failed: {exc}")
        )


def _safe_post_comment(gh: GitHubClient, pr_number: int, comment: str) -> None:
    """Attempts to post a comment on a PR; logs the error if it fails.

    Args:
        gh: GitHub client instance.
        pr_number: Pull request number.
        comment: Comment text to post.
    """
    try:
        gh.post_comment(pr_number, comment)
    except GitHubError as exc:
        logger.error("Failed to post comment on PR: %s", exc)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parses CLI arguments.

    Args:
        argv: Command-line arguments (overrides sys.argv in tests).

    Returns:
        Parsed argument namespace.
    """
    parser = argparse.ArgumentParser(
        description="AI Code Review Agent — automatically reviews pull requests.",
    )
    parser.add_argument(
        "--pr",
        type=int,
        required=True,
        help="Pull request number to review",
    )
    parser.add_argument(
        "--mode",
        choices=["base", "security", "performance", "multi-agent"],
        default="base",
        help="Review mode (default: base)",
    )
    parser.add_argument(
        "--instructions",
        type=str,
        default=None,
        help="PM instructions (optional)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    args = _parse_args(argv)
    if args.mode == "multi-agent":
        run_multi_agent(pr_number=args.pr, pm_instructions=args.instructions)
        return

    result = run(
        pr_number=args.pr,
        mode=args.mode,
        pm_instructions=args.instructions,
    )
    if result is None:
        sys.exit(1)


if __name__ == "__main__":
    main()


def run_chat(pr_number: int, comment_body: str) -> dict:
    from agent.github_client import GitHubClient

    gh = GitHubClient()
    pr_context = gh.get_pull_full_context(pr_number)

    chat_prompt = f"The developer asked you a question about PR #{pr_number}\n"
    chat_prompt += f"Q: {comment_body}\n\nDiff Context:\n{pr_context.get('diff', '')}\n"
    chat_prompt += (
        "Please provide a helpful code-related response directly answering the developer."
    )

    llm = _build_llm_client()
    answer = llm._call_api(chat_prompt)
    gh.post_comment(pr_number, f"### Contextual Answer\n\n{answer}")
    return {"reply": answer}
