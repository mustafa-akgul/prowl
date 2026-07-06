"""Tests for the Review Agent orchestrator."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent.review_agent import run


@pytest.fixture
def mock_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mocks all required environment variables."""
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setenv("GITHUB_REPO", "testorg/testrepo")
    monkeypatch.setenv("JIRA_URL", "https://test.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "test@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "fake-jira-token")
    monkeypatch.setenv("JIRA_REVIEW_STATUS", "In Review")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")


class TestReviewAgentWithJiraKey:
    """Tests for the scenario where a Jira key is found."""

    @patch("agent.review_agent._build_llm_client")
    @patch("agent.review_agent.JiraClient")
    @patch("agent.review_agent.GitHubClient")
    def test_full_flow_with_jira_key(
        self,
        mock_gh_cls: MagicMock,
        mock_jira_cls: MagicMock,
        mock_build_llm: MagicMock,
        mock_env: None,
    ) -> None:
        """When a Jira key is present: ticket is read, review is generated, PR and Jira are updated."""
        # GitHub mock
        mock_gh = MagicMock()
        mock_gh_cls.return_value = mock_gh
        mock_gh.get_pr_description.return_value = {
            "title": "PROJ-42 Fix login bug",
            "body": "Fixed the login page issue",
        }
        mock_gh.get_pr_diff.return_value = "--- a/auth.py\n+++ b/auth.py\n@@ -1 +1 @@\n-old\n+new"

        # Jira mock
        mock_jira = MagicMock()
        mock_jira_cls.return_value = mock_jira
        mock_jira.get_ticket.return_value = {
            "key": "PROJ-42",
            "summary": "Login bug fix",
            "description": "Login is broken",
            "acceptance_criteria": "User should be able to log in",
            "pm_notes": [],
        }

        # LLM client mock
        mock_llm = MagicMock()
        mock_build_llm.return_value = mock_llm
        mock_llm.review.return_value = "### Summary\nCode quality is good."

        result = run(pr_number=42, mode="base")

        # Review result should be returned
        assert result is not None
        assert "Code quality is good" in result[0]

        # PR comment should be posted
        mock_gh.post_comment.assert_called_once()
        comment_text = mock_gh.post_comment.call_args[0][1]
        assert "AI Code Review" in comment_text

        # Jira should be updated
        mock_jira.add_comment.assert_called_once()
        mock_jira.transition_status.assert_called_once()


class TestReviewAgentWithoutJiraKey:
    """Tests for the scenario where no Jira key is found."""

    @patch("agent.review_agent._build_llm_client")
    @patch("agent.review_agent.GitHubClient")
    def test_flow_without_jira_key(
        self,
        mock_gh_cls: MagicMock,
        mock_build_llm: MagicMock,
        mock_env: None,
    ) -> None:
        """Without a Jira key: review proceeds with a warning; Jira is not updated."""
        # GitHub mock — no Jira key
        mock_gh = MagicMock()
        mock_gh_cls.return_value = mock_gh
        mock_gh.get_pr_description.return_value = {
            "title": "Fix some bug",
            "body": "Details here",
        }
        mock_gh.get_pr_diff.return_value = "--- a/fix.py\n+++ b/fix.py\n@@ -1 +1 @@\n-x\n+y"

        # LLM client mock
        mock_llm = MagicMock()
        mock_build_llm.return_value = mock_llm
        mock_llm.review.return_value = "### Summary\nGenerally looks good."

        result = run(pr_number=99, mode="base")

        # Review result should be returned
        assert result is not None

        # PR comment should include the no-Jira warning
        mock_gh.post_comment.assert_called_once()
        comment_text = mock_gh.post_comment.call_args[0][1]
        assert "No Jira ticket" in comment_text


class TestReviewAgentErrors:
    """Tests for error scenarios."""

    @patch("agent.review_agent.GitHubClient")
    def test_github_connection_error(
        self,
        mock_gh_cls: MagicMock,
        mock_env: None,
    ) -> None:
        """Should return None when GitHub connection fails."""
        from agent.github_client import GitHubError

        mock_gh_cls.side_effect = GitHubError("Connection failed")

        result = run(pr_number=1, mode="base")

        assert result is None

    @patch("agent.review_agent._build_llm_client")
    @patch("agent.review_agent.GitHubClient")
    def test_llm_api_error(
        self,
        mock_gh_cls: MagicMock,
        mock_build_llm: MagicMock,
        mock_env: None,
    ) -> None:
        """Should return None and post an error comment when the LLM API fails."""
        from agent.base_client import LLMError

        mock_gh = MagicMock()
        mock_gh_cls.return_value = mock_gh
        mock_gh.get_pr_description.return_value = {
            "title": "Fix bug",
            "body": "",
        }
        mock_gh.get_pr_diff.return_value = "diff content"

        mock_build_llm.return_value.review.side_effect = LLMError("API error")

        result = run(pr_number=5, mode="base")

        assert result is None
        # An error comment should be posted to the PR
        mock_gh.post_comment.assert_called()
        error_comment = mock_gh.post_comment.call_args[0][1]
        assert "error" in error_comment.lower()
