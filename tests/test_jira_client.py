"""Tests for the Jira client module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent.jira_client import JiraClient, JiraError


@pytest.fixture
def mock_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mocks Jira environment variables."""
    monkeypatch.setenv("JIRA_URL", "https://test.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "test@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "fake-token")
    monkeypatch.setenv("JIRA_REVIEW_STATUS", "In Review")


@pytest.fixture
def jira_client(mock_env: None) -> JiraClient:
    """Creates a JiraClient with a mocked Jira connection."""
    with patch("agent.jira_client.Jira") as mock_jira_cls:
        mock_jira_cls.return_value = MagicMock()
        client = JiraClient()
        return client


class TestJiraClientInit:
    """Tests for JiraClient initialization."""

    def test_missing_env_raises_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should raise JiraError when required environment variables are missing."""
        monkeypatch.delenv("JIRA_URL", raising=False)
        monkeypatch.delenv("JIRA_EMAIL", raising=False)
        monkeypatch.delenv("JIRA_API_TOKEN", raising=False)

        with pytest.raises(JiraError, match="environment variable"):
            JiraClient()


class TestGetTicket:
    """Tests for the get_ticket method."""

    def test_get_ticket_success(self, jira_client: JiraClient) -> None:
        """Should return ticket information correctly."""
        jira_client.client.issue.return_value = {
            "fields": {
                "summary": "Fix login page",
                "description": "Login form is broken",
                "customfield_10001": "User should be able to log in",
                "comment": {
                    "comments": [
                        {"body": "PM note: fix urgently"},
                    ]
                },
            }
        }

        result = jira_client.get_ticket("PROJ-123")

        assert result["key"] == "PROJ-123"
        assert result["summary"] == "Fix login page"
        assert result["description"] == "Login form is broken"
        assert result["acceptance_criteria"] == "User should be able to log in"
        jira_client.client.issue.assert_called_once_with("PROJ-123")

    def test_get_ticket_api_error(self, jira_client: JiraClient) -> None:
        """Should raise JiraError on API failure."""
        jira_client.client.issue.side_effect = Exception("Connection timeout")

        with pytest.raises(JiraError, match="Failed to read"):
            jira_client.get_ticket("PROJ-999")


class TestAddComment:
    """Tests for the add_comment method."""

    def test_add_comment_success(self, jira_client: JiraClient) -> None:
        """Should add a comment to the ticket successfully."""
        jira_client.add_comment("PROJ-123", "Review completed")

        jira_client.client.issue_add_comment.assert_called_once_with("PROJ-123", "Review completed")

    def test_add_comment_api_error(self, jira_client: JiraClient) -> None:
        """Should raise JiraError on API failure."""
        jira_client.client.issue_add_comment.side_effect = Exception("Forbidden")

        with pytest.raises(JiraError, match="Failed to add comment"):
            jira_client.add_comment("PROJ-123", "Test")


class TestTransitionStatus:
    """Tests for the transition_status method."""

    def test_transition_success(self, jira_client: JiraClient) -> None:
        """Should apply the transition successfully."""
        jira_client.client.get_issue_transitions.return_value = [
            {"id": "31", "name": "In Review"},
            {"id": "41", "name": "Done"},
        ]

        jira_client.transition_status("PROJ-123")

        jira_client.client.set_issue_status.assert_called_once_with("PROJ-123", "In Review")

    def test_transition_not_found(self, jira_client: JiraClient) -> None:
        """Should raise JiraError when the target transition does not exist."""
        jira_client.client.get_issue_transitions.return_value = [
            {"id": "41", "name": "Done"},
        ]

        with pytest.raises(JiraError, match="not found"):
            jira_client.transition_status("PROJ-123")

    def test_transition_custom_status(self, jira_client: JiraClient) -> None:
        """Should use a custom status parameter when provided."""
        jira_client.client.get_issue_transitions.return_value = [
            {"id": "51", "name": "QA Ready"},
        ]

        jira_client.transition_status("PROJ-123", "QA Ready")

        jira_client.client.set_issue_status.assert_called_once_with("PROJ-123", "QA Ready")
