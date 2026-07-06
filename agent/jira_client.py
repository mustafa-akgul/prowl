"""Jira client module.

Handles communication with the Jira API: reading tickets, posting comments,
and transitioning ticket statuses.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from atlassian import Jira

logger = logging.getLogger(__name__)


class JiraError(Exception):
    """Exception raised for Jira API errors."""


class JiraClient:
    """Client for Jira ticket read/write operations.

    Attributes:
        client: ``atlassian-python-api`` Jira instance.
        review_status: Target status name to transition tickets into.
    """

    def __init__(self) -> None:
        jira_url = os.getenv("JIRA_URL")
        jira_email = os.getenv("JIRA_EMAIL")
        jira_token = os.getenv("JIRA_API_TOKEN")

        if not all([jira_url, jira_email, jira_token]):
            raise JiraError(
                "JIRA_URL, JIRA_EMAIL, and JIRA_API_TOKEN environment variables must be set."
            )

        self.client = Jira(
            url=jira_url,
            username=jira_email,
            password=jira_token,
            cloud=True,
        )
        self.review_status: str = os.getenv("JIRA_REVIEW_STATUS", "In Review")

    def get_ticket(self, jira_key: str) -> dict[str, Any]:
        """Returns ticket information for the given Jira key.

        Args:
            jira_key: Jira ticket key (e.g. ``PROJ-123``).

        Returns:
            Dictionary with keys: ``key``, ``summary``, ``description``,
            ``acceptance_criteria``, ``pm_notes``, ``sub_tasks``,
            and ``all_comments``.

        Raises:
            JiraError: If the ticket cannot be read or the API returns an error.
        """
        try:
            issue = self.client.issue(jira_key)
        except Exception as exc:
            raise JiraError(f"Failed to read Jira ticket '{jira_key}': {exc}") from exc

        fields = issue.get("fields", {})

        from agent.config_manager import get_api_config

        ac_field = get_api_config().get("jira_ac_field", "customfield_10001")

        # All comments chronologically
        raw_comments = fields.get("comment", {}).get("comments", [])
        all_comments: list[dict[str, str]] = []
        for c in raw_comments:
            author = c.get("author", {})
            all_comments.append(
                {
                    "author": author.get("displayName", author.get("name", "")),
                    "body": c.get("body", ""),
                    "created": c.get("created", ""),
                }
            )
        pm_notes = raw_comments[-1:] if raw_comments else []

        # Sub-tasks
        sub_tasks: list[dict[str, str]] = []
        for st in fields.get("subtasks", []):
            st_fields = st.get("fields", {})
            sub_tasks.append(
                {
                    "key": st.get("key", ""),
                    "summary": st_fields.get("summary", ""),
                    "status": st_fields.get("status", {}).get("name", ""),
                }
            )

        return {
            "key": jira_key,
            "summary": fields.get("summary", ""),
            "description": fields.get("description", ""),
            "acceptance_criteria": fields.get(ac_field, ""),
            "pm_notes": pm_notes,
            "sub_tasks": sub_tasks,
            "all_comments": all_comments,
        }

    def get_ticket_full_context(self, jira_key: str) -> str:
        """Returns a formatted string with full ticket context for LLM prompts.

        Includes summary, description, acceptance criteria, sub-tasks,
        and all developer/PM comments.

        Args:
            jira_key: Jira ticket key.

        Returns:
            Formatted context string suitable for LLM prompt injection.

        Raises:
            JiraError: If the ticket cannot be read.
        """
        ticket = self.get_ticket(jira_key)

        parts: list[str] = [
            f"**Ticket:** {ticket['key']}",
            f"**Summary:** {ticket['summary']}",
            f"**Description:** {ticket['description']}",
            f"**Acceptance Criteria:** {ticket['acceptance_criteria']}",
        ]

        if ticket["sub_tasks"]:
            parts.append("\n**Sub-Tasks:**")
            for st in ticket["sub_tasks"]:
                parts.append(f"  - [{st['status']}] {st['key']}: {st['summary']}")

        if ticket["all_comments"]:
            parts.append("\n**Developer/PM Comments:**")
            for c in ticket["all_comments"]:
                parts.append(f"  - **{c['author']}** ({c['created']}): {c['body'][:300]}")

        return "\n".join(parts)

    def add_comment(self, jira_key: str, comment: str) -> None:
        """Adds a comment to a Jira ticket.

        Args:
            jira_key: Jira ticket key.
            comment: Comment text to add.

        Raises:
            JiraError: If the comment cannot be posted.
        """
        try:
            self.client.issue_add_comment(jira_key, comment)
            logger.info("Comment added to Jira ticket '%s'.", jira_key)
        except Exception as exc:
            raise JiraError(f"Failed to add comment to Jira ticket '{jira_key}': {exc}") from exc

    def transition_status(self, jira_key: str, status: str | None = None) -> None:
        """Transitions a Jira ticket to the specified status.

        Queries available transitions from the Jira API and applies the one
        matching the target status name.

        Args:
            jira_key: Jira ticket key.
            status: Target status name. Uses ``self.review_status`` if ``None``.

        Raises:
            JiraError: If the transition is not found or cannot be applied.
        """
        target_status = status or self.review_status

        try:
            transitions = self.client.get_issue_transitions(jira_key)
        except Exception as exc:
            raise JiraError(
                f"Failed to retrieve transitions for Jira ticket '{jira_key}': {exc}"
            ) from exc

        transition_id: str | None = None
        for transition in transitions:
            if transition.get("name", "").lower() == target_status.lower():
                transition_id = transition["id"]
                break

        if transition_id is None:
            available = [t.get("name") for t in transitions]
            raise JiraError(
                f"Transition '{target_status}' not found. Available transitions: {available}"
            )

        try:
            self.client.set_issue_status(jira_key, target_status)
            logger.info(
                "Jira ticket '%s' status updated to '%s'.",
                jira_key,
                target_status,
            )
        except Exception as exc:
            raise JiraError(f"Failed to update status of Jira ticket '{jira_key}': {exc}") from exc
