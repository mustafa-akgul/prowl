"""Base LLM Client — shared base class for all LLM clients.

Provides common template loading, placeholder substitution, retry delay
parsing, and diff chunking logic. Subclasses implement the review() method
with their respective API calls.
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from typing import Literal

from agent.config_manager import load_prompt

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """Base exception for LLM API errors."""


class BaseLLMClient(ABC):
    """Abstract base class for Gemini and other LLM clients.

    Attributes:
        max_tokens: Maximum number of tokens in the response.
        temperature: LLM temperature setting.
        max_retries: Maximum number of retry attempts on transient errors.
        initial_backoff: Initial backoff delay in seconds for retry logic.
    """

    def __init__(self, max_tokens: int = 2048, temperature: float = 0.3) -> None:
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.max_retries = 3
        self.initial_backoff = 2
        self.usage_stats = {"prompt_tokens": 0, "completion_tokens": 0}

    @staticmethod
    def _load_template(mode: str) -> str:
        """Loads a prompt template via ConfigManager.

        Args:
            mode: Review mode identifier.

        Returns:
            Template content string.

        Raises:
            LLMError: If the template is missing or empty.
        """
        template = load_prompt(mode)
        if not template:
            raise LLMError(f"Prompt template not found or empty: '{mode}'")
        return template

    @staticmethod
    def _extract_retry_delay(error_msg: str, fallback: float) -> float:
        """Parses a suggested retry delay from an API error message.

        Looks for patterns like ``retry in 54s`` or ``'retryDelay': '54s'``
        in the error string. Returns ``fallback`` if no match is found.

        Args:
            error_msg: Error message string from the API.
            fallback: Delay to use when no suggestion is found, in seconds.

        Returns:
            Suggested wait time in seconds (API value + 1 s safety margin),
            or the fallback value.
        """
        # Pattern: "retry in 54s"
        match = re.search(r"retry in ([\d\.]+)s", error_msg)
        if match:
            return float(match.group(1)) + 1

        # Pattern: "'retryDelay': '54s'"
        match = re.search(r"'retryDelay':\s*'(\d+)s'", error_msg)
        if match:
            return float(match.group(1)) + 1

        return fallback

    def _prepare_prompt(
        self,
        diff: str,
        jira_context: str,
        pm_instructions: str,
        mode: str,
        corporate_context: str = "",
    ) -> str:
        """Fills template placeholders with the provided values.

        Args:
            diff: PR diff string.
            jira_context: Jira ticket context text.
            pm_instructions: Additional instructions from the PM.
            mode: Review mode identifier.
            corporate_context: RAG-retrieved corporate knowledge context.

        Returns:
            Fully rendered prompt string.
        """
        template = self._load_template(mode)
        return template.format(
            diff=diff,
            jira_context=jira_context or "No Jira ticket information available.",
            pm_instructions=pm_instructions or "No additional instructions.",
            corporate_context=corporate_context or "No corporate knowledge context available.",
        )

    @abstractmethod
    def _call_api(self, prompt: str) -> str:
        """Sends a prompt to the LLM and returns the generated text.

        Subclasses must implement the actual API call logic here,
        including any retry/fallback behavior specific to the provider.

        Args:
            prompt: Full prompt string.

        Returns:
            Generated text from the model.
        """

    def review(
        self,
        diff: str,
        jira_context: str,
        pm_instructions: str,
        mode: Literal["base", "security", "performance"] = "base",
        corporate_context: str = "",
    ) -> str:
        """Generates a code review for a PR diff (template method).

        Handles diff chunking and result formatting automatically.
        Subclasses provide the API call logic via ``_call_api()``.

        Args:
            diff: PR diff string.
            jira_context: Jira ticket context text.
            pm_instructions: Additional instructions from the PM.
            mode: Review mode.
            corporate_context: RAG-retrieved corporate knowledge context.

        Returns:
            Generated review text.
        """
        diff_chunks = self.chunk_diff(diff, max_chars=20000)

        if len(diff_chunks) > 1:
            logger.info("Large diff detected — split into %d parts.", len(diff_chunks))

        combined_reviews: list[str] = []

        for i, chunk in enumerate(diff_chunks):
            prompt = self._prepare_prompt(
                chunk, jira_context, pm_instructions, mode, corporate_context
            )
            text = self._call_api(prompt)

            if len(diff_chunks) > 1:
                combined_reviews.append(f"### Part {i + 1}/{len(diff_chunks)}\n\n{text}")
            else:
                combined_reviews.append(text)

        return "\n\n---\n\n".join(combined_reviews)

    def chunk_diff(self, diff: str, max_chars: int = 20000) -> list[str]:
        """Splits a large diff into chunks suitable for LLM context limits.

        Uses a simple line-based splitting strategy.

        Args:
            diff: Full diff string.
            max_chars: Maximum characters per chunk.

        Returns:
            List of diff chunks.
        """
        if len(diff) <= max_chars:
            return [diff]

        chunks = []
        lines = diff.splitlines()
        current_chunk: list[str] = []
        current_len = 0

        for line in lines:
            if current_len + len(line) > max_chars and current_chunk:
                chunks.append("\n".join(current_chunk))
                current_chunk = []
                current_len = 0
            current_chunk.append(line)
            current_len += len(line) + 1  # +1 for the '\n' added by join()

        if current_chunk:
            chunks.append("\n".join(current_chunk))
        return chunks
