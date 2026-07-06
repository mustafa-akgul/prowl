"""Notifier — sends review reports via email and webhook channels.

Supports multiple notification channels:
- SMTP email (with HTML attachment)
- Slack webhook
- Microsoft Teams webhook
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
import urllib.error
import urllib.request
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class NotifierError(Exception):
    """Exception raised for notification errors."""


class EmailNotifier:
    """Sends review reports via SMTP email.

    Reads SMTP configuration from environment variables:
    - ``SMTP_HOST`` — SMTP server hostname
    - ``SMTP_PORT`` — SMTP port (default: 587)
    - ``SMTP_USER`` — SMTP username
    - ``SMTP_PASSWORD`` — SMTP password
    - ``SMTP_FROM`` — Sender email address
    - ``SMTP_USE_TLS`` — Enable TLS (default: true)
    """

    def __init__(self) -> None:
        """Initializes the email notifier from environment variables."""
        self.host = os.getenv("SMTP_HOST", "")
        self.port = int(os.getenv("SMTP_PORT", "587"))
        self.user = os.getenv("SMTP_USER", "")
        self.password = os.getenv("SMTP_PASSWORD", "")
        self.from_addr = os.getenv("SMTP_FROM", self.user)
        self.use_tls = os.getenv("SMTP_USE_TLS", "true").lower() == "true"

    @property
    def is_configured(self) -> bool:
        """Returns True if SMTP is configured."""
        return bool(self.host and self.user and self.password)

    def send(
        self,
        to_addrs: list[str],
        subject: str,
        body: str,
        html_body: str = "",
        attachments: list[str] | None = None,
    ) -> None:
        """Sends an email notification.

        Args:
            to_addrs: List of recipient email addresses.
            subject: Email subject line.
            body: Plain text email body.
            html_body: Optional HTML email body.
            attachments: Optional list of file paths to attach.

        Raises:
            NotifierError: If the email cannot be sent.
        """
        if not self.is_configured:
            raise NotifierError("SMTP not configured. Set SMTP_HOST, SMTP_USER, SMTP_PASSWORD.")

        msg = MIMEMultipart("alternative")
        msg["From"] = self.from_addr
        msg["To"] = ", ".join(to_addrs)
        msg["Subject"] = subject

        msg.attach(MIMEText(body, "plain", "utf-8"))
        if html_body:
            msg.attach(MIMEText(html_body, "html", "utf-8"))

        # Add file attachments
        if attachments:
            for filepath in attachments:
                path = Path(filepath)
                if path.exists():
                    part = MIMEBase("application", "octet-stream")
                    part.set_payload(path.read_bytes())
                    encoders.encode_base64(part)
                    part.add_header(
                        "Content-Disposition",
                        f"attachment; filename={path.name}",
                    )
                    msg.attach(part)

        try:
            with smtplib.SMTP(self.host, self.port) as server:
                if self.use_tls:
                    server.starttls()
                server.login(self.user, self.password)
                server.send_message(msg, self.from_addr, to_addrs)
            logger.info("Email sent to: %s", ", ".join(to_addrs))
        except Exception as exc:
            raise NotifierError(f"Failed to send email: {exc}") from exc


class SlackNotifier:
    """Sends review notifications to Slack via webhook.

    Reads the webhook URL from ``SLACK_WEBHOOK_URL`` environment variable.
    """

    def __init__(self) -> None:
        """Initializes the Slack notifier."""
        self.webhook_url = os.getenv("SLACK_WEBHOOK_URL", "")

    @property
    def is_configured(self) -> bool:
        """Returns True if Slack webhook is configured."""
        return bool(self.webhook_url)

    def send(
        self,
        pr_number: int,
        mode: str,
        review_summary: str,
        pr_url: str = "",
    ) -> None:
        """Sends a Slack notification.

        Args:
            pr_number: Pull request number.
            mode: Review mode used.
            review_summary: Brief review summary (first 500 chars).
            pr_url: URL to the pull request.

        Raises:
            NotifierError: If the notification cannot be sent.
        """
        if not self.is_configured:
            raise NotifierError("SLACK_WEBHOOK_URL not configured.")

        # Build Slack Block Kit message
        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"🤖 Code Review — PR #{pr_number} ({mode})",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": review_summary[:2000],
                },
            },
        ]

        if pr_url:
            blocks.append(
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "View PR on GitHub"},
                            "url": pr_url,
                        },
                    ],
                }
            )

        payload = json.dumps({"blocks": blocks}).encode("utf-8")

        try:
            req = urllib.request.Request(
                self.webhook_url,
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req)
            logger.info("Slack notification sent for PR #%d.", pr_number)
        except urllib.error.URLError as exc:
            raise NotifierError(f"Slack notification failed: {exc}") from exc


class TeamsNotifier:
    """Sends review notifications to Microsoft Teams via webhook.

    Reads the webhook URL from ``TEAMS_WEBHOOK_URL`` environment variable.
    """

    def __init__(self) -> None:
        """Initializes the Teams notifier."""
        self.webhook_url = os.getenv("TEAMS_WEBHOOK_URL", "")

    @property
    def is_configured(self) -> bool:
        """Returns True if Teams webhook is configured."""
        return bool(self.webhook_url)

    def send(
        self,
        pr_number: int,
        mode: str,
        review_summary: str,
        pr_url: str = "",
    ) -> None:
        """Sends a Teams notification.

        Args:
            pr_number: Pull request number.
            mode: Review mode used.
            review_summary: Brief review summary.
            pr_url: URL to the pull request.

        Raises:
            NotifierError: If the notification cannot be sent.
        """
        if not self.is_configured:
            raise NotifierError("TEAMS_WEBHOOK_URL not configured.")

        card = {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "themeColor": "0076D7",
            "summary": f"Code Review — PR #{pr_number}",
            "sections": [
                {
                    "activityTitle": f"🤖 Code Review — PR #{pr_number} ({mode})",
                    "text": review_summary[:2000],
                    "markdown": True,
                },
            ],
        }

        if pr_url:
            card["potentialAction"] = [
                {
                    "@type": "OpenUri",
                    "name": "View PR on GitHub",
                    "targets": [{"os": "default", "uri": pr_url}],
                },
            ]

        payload = json.dumps(card).encode("utf-8")

        try:
            req = urllib.request.Request(
                self.webhook_url,
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req)
            logger.info("Teams notification sent for PR #%d.", pr_number)
        except urllib.error.URLError as exc:
            raise NotifierError(f"Teams notification failed: {exc}") from exc


def send_review_notification(
    pr_number: int,
    mode: str,
    review_text: str,
    pr_url: str = "",
    email_recipients: list[str] | None = None,
    html_report_path: str | None = None,
    pdf_report_path: str | None = None,
) -> dict[str, bool]:
    """Sends review notifications via all configured channels.

    Attempts to send via each configured channel and reports success/failure
    for each.

    Args:
        pr_number: Pull request number.
        mode: Review mode used.
        review_text: Full review text.
        pr_url: URL to the pull request.
        email_recipients: List of email addresses (overrides env var).
        html_report_path: Path to HTML report attachment.
        pdf_report_path: Path to PDF report attachment.

    Returns:
        Dictionary mapping channel name to success boolean.
    """
    results: dict[str, bool] = {}

    # Summary for chat notifications (first 500 chars)
    summary = review_text[:500] + ("..." if len(review_text) > 500 else "")

    # Email
    email = EmailNotifier()
    if email.is_configured:
        recipients = email_recipients or os.getenv("REVIEW_EMAIL_RECIPIENTS", "").split(",")
        recipients = [r.strip() for r in recipients if r.strip()]

        if recipients:
            attachments: list[str] = []
            if pdf_report_path:
                attachments.append(pdf_report_path)
            elif html_report_path:
                attachments.append(html_report_path)

            try:
                email.send(
                    to_addrs=recipients,
                    subject=f"Code Review Report — PR #{pr_number} ({mode})",
                    body=review_text,
                    attachments=attachments,
                )
                results["email"] = True
            except NotifierError as exc:
                logger.error("Email notification failed: %s", exc)
                results["email"] = False
        else:
            results["email"] = False
    else:
        results["email"] = False

    # Slack
    slack = SlackNotifier()
    if slack.is_configured:
        try:
            slack.send(pr_number, mode, summary, pr_url)
            results["slack"] = True
        except NotifierError as exc:
            logger.error("Slack notification failed: %s", exc)
            results["slack"] = False
    else:
        results["slack"] = False

    # Teams
    teams = TeamsNotifier()
    if teams.is_configured:
        try:
            teams.send(pr_number, mode, summary, pr_url)
            results["teams"] = True
        except NotifierError as exc:
            logger.error("Teams notification failed: %s", exc)
            results["teams"] = False
    else:
        results["teams"] = False

    return results
