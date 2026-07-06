"""Tests for the GitHub client module."""

from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from agent.github_client import GitHubClient


@pytest.fixture
def mock_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mocks GitHub environment variables."""
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setenv("GITHUB_REPO", "testorg/testrepo")


@pytest.fixture
def github_client(mock_env: None) -> GitHubClient:
    """Creates a GitHubClient with a mocked GitHub connection."""
    with patch("agent.github_client.Github") as mock_github_cls:
        mock_repo = MagicMock()
        mock_github_cls.return_value.get_repo.return_value = mock_repo
        client = GitHubClient()
        return client


# ──────────────────────────────────────────────
# extract_jira_key tests
# ──────────────────────────────────────────────


class TestExtractJiraKey:
    """Tests for the extract_jira_key static method."""

    @pytest.mark.parametrize(
        "text, expected",
        [
            ("PROJ-123 fix login bug", "PROJ-123"),
            ("feat: ABC-1 initial commit", "ABC-1"),
            ("[TEAM-4567] refactor auth module", "TEAM-4567"),
            ("fix(CORE-99): handle edge case", "CORE-99"),
            ("Multiple keys DATA-1 and DATA-2", "DATA-1"),  # First match
        ],
    )
    def test_valid_jira_keys(self, text: str, expected: str) -> None:
        """Should correctly extract valid Jira key formats."""
        assert GitHubClient.extract_jira_key(text) == expected

    @pytest.mark.parametrize(
        "text",
        [
            "proj-123",  # lowercase
            "123",  # digits only
            "PROJ123",  # missing hyphen
            "no jira key here",
            "",  # empty string
            "PROJ-",  # missing digits
            "-123",  # missing letters
        ],
    )
    def test_invalid_jira_keys(self, text: str) -> None:
        """Should return None for invalid formats."""
        assert GitHubClient.extract_jira_key(text) is None


# ──────────────────────────────────────────────
# get_pr_diff tests
# ──────────────────────────────────────────────


class TestGetPrDiff:
    """Tests for the get_pr_diff method."""

    def test_get_pr_diff_success(self, github_client: GitHubClient) -> None:
        """Should return a combined diff string for all changed files."""
        mock_file_1 = MagicMock()
        mock_file_1.filename = "src/auth.py"
        mock_file_1.patch = "@@ -1,3 +1,4 @@\n import os\n+import jwt\n"

        mock_file_2 = MagicMock()
        mock_file_2.filename = "src/utils.py"
        mock_file_2.patch = "@@ -10,2 +10,3 @@\n def helper():\n+    pass\n"

        mock_pull = MagicMock()
        mock_pull.get_files.return_value = [mock_file_1, mock_file_2]
        github_client.repo.get_pull.return_value = mock_pull

        diff = github_client.get_pr_diff(42)

        assert "src/auth.py" in diff
        assert "src/utils.py" in diff
        assert "+import jwt" in diff
        github_client.repo.get_pull.assert_called_once_with(42)

    def test_get_pr_diff_binary_file(self, github_client: GitHubClient) -> None:
        """Should show a descriptive placeholder when a file has no patch (binary)."""
        mock_file = MagicMock()
        mock_file.filename = "image.png"
        mock_file.patch = None  # binary files have no patch

        mock_pull = MagicMock()
        mock_pull.get_files.return_value = [mock_file]
        github_client.repo.get_pull.return_value = mock_pull

        diff = github_client.get_pr_diff(42)

        assert "image.png" in diff
        assert "binary" in diff.lower()


# ──────────────────────────────────────────────
# get_pr_description tests
# ──────────────────────────────────────────────


class TestGetPrDescription:
    """Tests for the get_pr_description method."""

    def test_get_pr_description_success(self, github_client: GitHubClient) -> None:
        """Should return the PR title and body."""
        mock_pull = MagicMock()
        type(mock_pull).title = PropertyMock(return_value="PROJ-42 Fix auth")
        type(mock_pull).body = PropertyMock(return_value="Fixed the auth module")
        github_client.repo.get_pull.return_value = mock_pull

        result = github_client.get_pr_description(42)

        assert result["title"] == "PROJ-42 Fix auth"
        assert result["body"] == "Fixed the auth module"


# ──────────────────────────────────────────────
# post_comment tests
# ──────────────────────────────────────────────


class TestPostComment:
    """Tests for the post_comment method."""

    def test_post_comment_success(self, github_client: GitHubClient) -> None:
        """Should post a comment on the PR successfully."""
        mock_pull = MagicMock()
        github_client.repo.get_pull.return_value = mock_pull

        github_client.post_comment(42, "Great work!")

        mock_pull.create_issue_comment.assert_called_once_with("Great work!")
