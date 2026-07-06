"""GitHub client module.

Handles communication with the GitHub API: retrieving PR diffs and
descriptions, extracting Jira keys, and posting PR comments.
"""

from __future__ import annotations

import itertools
import logging
import os
import re

from github import Github, GithubException

logger = logging.getLogger(__name__)

# Jira key format: one or more uppercase letters, a hyphen, one or more digits
JIRA_KEY_PATTERN = re.compile(r"[A-Z]+-\d+")

MAX_COMMENT_PREVIEW_CHARS = 200
RECENT_COMMENTS_LIMIT = 10
MAX_REVIEW_COMMENTS_LIMIT = 50
MAX_COMMITS_LIMIT = 100
MAX_REPO_LIST_LIMIT = 50


class GitHubError(Exception):
    """Exception raised for GitHub API errors."""


class GitHubClient:
    """Client for GitHub PR operations.

    Attributes:
        repo: ``PyGithub`` repository object.
    """

    def __init__(self) -> None:
        github_token = os.getenv("GITHUB_TOKEN")
        github_repo = os.getenv("GITHUB_REPO")

        if not github_token or not github_repo:
            raise GitHubError("GITHUB_TOKEN and GITHUB_REPO environment variables must be set.")

        try:
            self._github = Github(github_token)
            self.repo = self._github.get_repo(github_repo)
        except GithubException as exc:
            raise GitHubError(f"Failed to connect to GitHub repo '{github_repo}': {exc}") from exc

    def get_pr_diff(self, pr_number: int) -> str:
        """Returns the combined diff of all changed files in a PR.

        Each file's patch is concatenated into a single string.

        Args:
            pr_number: Pull request number.

        Returns:
            Combined diff string.

        Raises:
            GitHubError: If the PR is not found or the API returns an error.
        """
        try:
            pull = self.repo.get_pull(pr_number)
            files = pull.get_files()

            diff_parts: list[str] = []
            for file in files:
                header = f"--- a/{file.filename}\n+++ b/{file.filename}"
                patch = file.patch or "(binary file or empty diff)"
                diff_parts.append(f"{header}\n{patch}")

            return "\n\n".join(diff_parts)
        except GithubException as exc:
            raise GitHubError(f"Failed to retrieve diff for PR #{pr_number}: {exc}") from exc

    def get_pr_description(self, pr_number: int) -> dict[str, str]:
        """Returns the title and body of a pull request.

        Args:
            pr_number: Pull request number.

        Returns:
            Dictionary with ``title`` and ``body`` keys.

        Raises:
            GitHubError: If the PR is not found or the API returns an error.
        """
        try:
            pull = self.repo.get_pull(pr_number)
            return {
                "title": pull.title or "",
                "body": pull.body or "",
            }
        except GithubException as exc:
            raise GitHubError(f"Failed to retrieve description for PR #{pr_number}: {exc}") from exc

    def get_pull_full_context(self, pr_number: int) -> dict:
        """Returns title, body, diff, review comments, and commits.

        Provides deep context by fetching all relevant data from a single
        PR reference. Eliminates the need for multiple separate API calls.

        Args:
            pr_number: Pull request number.

        Returns:
            Dictionary with ``title``, ``body``, ``diff``,
            ``review_comments``, and ``commits`` keys.

        Raises:
            GitHubError: If the PR is not found or the API returns an error.
        """
        try:
            pull = self.repo.get_pull(pr_number)
            diff_parts: list[str] = []
            for file in pull.get_files():
                header = f"--- a/{file.filename}\n+++ b/{file.filename}"
                patch = file.patch or "(binary file or empty diff)"
                diff_parts.append(f"{header}\n{patch}")

            # Fetch review comments (inline code comments)
            review_comments: list[dict[str, str]] = []
            for rc in itertools.islice(pull.get_review_comments(), MAX_REVIEW_COMMENTS_LIMIT):
                review_comments.append(
                    {
                        "user": rc.user.login if rc.user else "",
                        "body": rc.body or "",
                        "path": rc.path or "",
                        "line": rc.original_line or rc.line,
                        "created_at": rc.created_at.strftime("%Y-%m-%d %H:%M")
                        if rc.created_at
                        else "",
                    }
                )

            # Fetch issue comments (regular PR comments)
            issue_comments: list[dict[str, str]] = []
            for ic in itertools.islice(pull.get_issue_comments(), MAX_REVIEW_COMMENTS_LIMIT):
                issue_comments.append(
                    {
                        "user": ic.user.login if ic.user else "",
                        "body": ic.body or "",
                        "created_at": ic.created_at.strftime("%Y-%m-%d %H:%M")
                        if ic.created_at
                        else "",
                    }
                )

            # Fetch commit messages (chronological)
            commits: list[dict[str, str]] = []
            for c in itertools.islice(pull.get_commits(), MAX_COMMITS_LIMIT):
                commits.append(
                    {
                        "sha": c.sha[:8],
                        "message": c.commit.message or "",
                        "author": c.commit.author.name if c.commit.author else "",
                        "date": c.commit.author.date.strftime("%Y-%m-%d %H:%M")
                        if c.commit.author and c.commit.author.date
                        else "",
                    }
                )

            return {
                "title": pull.title or "",
                "body": pull.body or "",
                "diff": "\n\n".join(diff_parts),
                "review_comments": review_comments,
                "issue_comments": issue_comments,
                "commits": commits,
            }
        except GithubException as exc:
            raise GitHubError(f"Failed to retrieve context for PR #{pr_number}: {exc}") from exc

    def get_pr_review_comments(self, pr_number: int) -> list[dict[str, str]]:
        """Returns inline review comments on a pull request.

        Args:
            pr_number: Pull request number.

        Returns:
            List of review comment dictionaries with user, body, path,
            line number, and creation date.

        Raises:
            GitHubError: If the comments cannot be retrieved.
        """
        try:
            pull = self.repo.get_pull(pr_number)
            comments: list[dict[str, str]] = []
            for rc in itertools.islice(pull.get_review_comments(), MAX_REVIEW_COMMENTS_LIMIT):
                comments.append(
                    {
                        "user": rc.user.login if rc.user else "",
                        "body": rc.body or "",
                        "path": rc.path or "",
                        "line": rc.original_line or rc.line,
                        "created_at": rc.created_at.strftime("%Y-%m-%d %H:%M")
                        if rc.created_at
                        else "",
                    }
                )
            return comments
        except GithubException as exc:
            raise GitHubError(
                f"Failed to retrieve review comments for PR #{pr_number}: {exc}"
            ) from exc

    def get_pr_commits(self, pr_number: int) -> list[dict[str, str]]:
        """Returns commit messages for a pull request in chronological order.

        Args:
            pr_number: Pull request number.

        Returns:
            List of commit dictionaries with sha, message, author, and date.

        Raises:
            GitHubError: If the commits cannot be retrieved.
        """
        try:
            pull = self.repo.get_pull(pr_number)
            commits: list[dict[str, str]] = []
            for c in itertools.islice(pull.get_commits(), MAX_COMMITS_LIMIT):
                commits.append(
                    {
                        "sha": c.sha[:8],
                        "message": c.commit.message or "",
                        "author": c.commit.author.name if c.commit.author else "",
                        "date": c.commit.author.date.strftime("%Y-%m-%d %H:%M")
                        if c.commit.author and c.commit.author.date
                        else "",
                    }
                )
            return commits
        except GithubException as exc:
            raise GitHubError(f"Failed to retrieve commits for PR #{pr_number}: {exc}") from exc

    @staticmethod
    def extract_jira_key(text: str) -> str | None:
        """Extracts a Jira ticket key from text.

        Returns the first match in ``PROJ-123`` format.

        Args:
            text: Text to search.

        Returns:
            Matching Jira key or ``None``.
        """
        match = JIRA_KEY_PATTERN.search(text)
        return match.group(0) if match else None

    def post_inline_suggestion(
        self, pr_number: int, commit_id: str, path: str, line: int, suggestion: str
    ) -> None:
        try:
            pull = self.repo.get_pull(pr_number)
            pull.create_review_comment(body=suggestion, commit_id=commit_id, path=path, line=line)
            logger.info("Inline suggestion posted on PR %d, %s:%d", pr_number, path, line)
        except Exception as exc:
            logger.error(f"Failed to post inline suggestion: {exc}")

    def post_comment(self, pr_number: int, comment: str) -> None:
        """Posts a markdown-formatted comment on a pull request.

        Args:
            pr_number: Pull request number.
            comment: Comment text (markdown supported).

        Raises:
            GitHubError: If the comment cannot be posted.
        """
        try:
            pull = self.repo.get_pull(pr_number)
            pull.create_issue_comment(comment)
            logger.info("Comment posted on PR #%d.", pr_number)
        except GithubException as exc:
            raise GitHubError(f"Failed to post comment on PR #{pr_number}: {exc}") from exc

    def list_user_repos(self) -> list[dict[str, str]]:
        """Lists all repositories accessible to the authenticated user.

        Returns:
            List of repository info dictionaries.

        Raises:
            GitHubError: If the API returns an error.
        """
        repos: list[dict[str, str]] = []
        try:
            for repo in itertools.islice(
                self._github.get_user().get_repos(sort="updated"), MAX_REPO_LIST_LIMIT
            ):
                repos.append(
                    {
                        "full_name": repo.full_name,
                        "name": repo.name,
                        "description": repo.description or "",
                        "language": repo.language or "",
                        "updated_at": repo.updated_at.strftime("%Y-%m-%d")
                        if repo.updated_at
                        else "",
                        "private": repo.private,
                        "url": repo.html_url,
                        "open_issues": repo.open_issues_count,
                    }
                )
            return repos
        except GithubException as exc:
            raise GitHubError(f"Failed to retrieve repository list: {exc}") from exc

    def get_file_content(self, filepath: str, ref: str) -> str:
        """Fetches the raw content of a file from the repository at a specific commit.

        Args:
            filepath: Path to the file in the repository.
            ref: The name of the commit/branch/tag.

        Returns:
            Decoded file content.

        Raises:
            GitHubError: If the file cannot be retrieved.
        """
        try:
            content_file = self.repo.get_contents(filepath, ref=ref)
            if isinstance(content_file, list):
                raise GitHubError(f"Path '{filepath}' is a directory, not a file.")
            # PyGithub's decoded_content is a bytes object
            return content_file.decoded_content.decode("utf-8")
        except GithubException as exc:
            raise GitHubError(f"Failed to retrieve file '{filepath}' at '{ref}': {exc}") from exc

    def get_pr_detail(self, pr_number: int) -> dict:
        """Returns detailed information about a pull request.

        Args:
            pr_number: Pull request number.

        Returns:
            Dictionary with PR metadata, files, reviews, and recent comments.

        Raises:
            GitHubError: If the PR is not found or the API returns an error.
        """
        try:
            pull = self.repo.get_pull(pr_number)
            files = []
            for f in pull.get_files():
                files.append(
                    {
                        "filename": f.filename,
                        "status": f.status,
                        "additions": f.additions,
                        "deletions": f.deletions,
                        "changes": f.changes,
                    }
                )
            reviews = []
            for r in pull.get_reviews():
                reviews.append(
                    {
                        "user": r.user.login if r.user else "",
                        "state": r.state,
                        "submitted_at": r.submitted_at.strftime("%Y-%m-%d %H:%M")
                        if r.submitted_at
                        else "",
                    }
                )
            comments = []
            for c in pull.get_issue_comments():
                comments.append(
                    {
                        "user": c.user.login if c.user else "",
                        "body": c.body[:MAX_COMMENT_PREVIEW_CHARS] if c.body else "",
                        "created_at": c.created_at.strftime("%Y-%m-%d %H:%M")
                        if c.created_at
                        else "",
                    }
                )
            return {
                "number": pull.number,
                "title": pull.title or "",
                "body": pull.body or "",
                "state": pull.state,
                "user": pull.user.login if pull.user else "",
                "user_avatar": pull.user.avatar_url if pull.user else "",
                "head": pull.head.ref if pull.head else "",
                "base": pull.base.ref if pull.base else "",
                "created_at": pull.created_at.strftime("%Y-%m-%d %H:%M") if pull.created_at else "",
                "updated_at": pull.updated_at.strftime("%Y-%m-%d %H:%M") if pull.updated_at else "",
                "merged": pull.merged,
                "mergeable": pull.mergeable,
                "labels": [label.name for label in pull.labels],
                "additions": pull.additions,
                "deletions": pull.deletions,
                "changed_files": pull.changed_files,
                "url": pull.html_url,
                "files": files,
                "reviews": reviews,
                "comments": comments[-RECENT_COMMENTS_LIMIT:],
            }
        except GithubException as exc:
            raise GitHubError(f"Failed to retrieve details for PR #{pr_number}: {exc}") from exc
