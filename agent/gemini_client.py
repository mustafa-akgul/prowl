"""Gemini (Google AI) client module.

Handles communication with the Google Gemini API: loads prompt templates,
fills placeholders, and produces code review output.
"""

from __future__ import annotations

import logging
import os
import time

from google import genai
from google.genai import types

from agent.base_client import BaseLLMClient, LLMError
from agent.config_manager import get_review_config

logger = logging.getLogger(__name__)

# Keywords that indicate a temporary or daily quota issue
_QUOTA_KEYWORDS = ("quota", "rate limit", "exhausted", "429", "resource_exhausted")


class GeminiError(LLMError):
    """Exception raised for Gemini API errors."""


class GeminiClient(BaseLLMClient):
    """Code review client backed by the Google Gemini API."""

    def __init__(self) -> None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise GeminiError("GEMINI_API_KEY is not set.")

        self.client = genai.Client(api_key=api_key)

        cfg = get_review_config()
        super().__init__(
            max_tokens=cfg.get("max_tokens", 2048),
            temperature=cfg.get("temperature", 0.3),
        )
        self.model = cfg.get("model", "gemini-2.0-flash")
        self.fallback_model = cfg.get("fallback_model", "gemini-1.5-flash")
        # Quota errors benefit from more attempts than the base default of 3
        self.max_retries = 5

    def _generate_with_retries(self, model: str, prompt: str) -> str:
        """Generates content for a single prompt with retry/backoff logic.

        Args:
            model: Gemini model name to use (e.g. ``"gemini-2.0-flash"``).
            prompt: Full prompt string passed to the model.

        Returns:
            Generated text from the model.

        Raises:
            GeminiError: On unrecoverable API errors or after all retries are
                exhausted due to quota/rate-limit errors.
        """
        last_exception: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        max_output_tokens=self.max_tokens,
                        temperature=self.temperature,
                    ),
                )
                if hasattr(response, "usage_metadata") and response.usage_metadata:
                    self.usage_stats["prompt_tokens"] += getattr(
                        response.usage_metadata, "prompt_token_count", 0
                    )
                    self.usage_stats["completion_tokens"] += getattr(
                        response.usage_metadata, "candidates_token_count", 0
                    )
                return response.text

            except Exception as exc:
                error_msg = str(exc)
                if any(x in error_msg.lower() for x in _QUOTA_KEYWORDS):
                    last_exception = exc
                    if attempt < self.max_retries:
                        wait_time = self._extract_retry_delay(
                            error_msg,
                            fallback=self.initial_backoff * (2 ** (attempt - 1)),
                        )
                        logger.warning(
                            "Rate limit hit on %s (attempt %d/%d). Waiting %ds...",
                            model,
                            attempt,
                            self.max_retries,
                            wait_time,
                        )
                        time.sleep(wait_time)
                        continue

                    # All retries exhausted with a quota/rate-limit error
                    raise GeminiError(
                        f"Quota/rate limit exhausted on {model} after "
                        f"{self.max_retries} attempts. "
                        f"Consider waiting or upgrading your API plan. "
                        f"Detail: {exc}"
                    ) from exc

                raise GeminiError(f"Gemini API error: {exc}") from exc

        raise GeminiError(
            f"No response from {model} after {self.max_retries} attempts: {last_exception}"
        )

    def _call_api(self, prompt: str) -> str:
        """Sends a prompt to Gemini with primary/fallback model logic.

        Tries the primary model first. If quota/rate-limit errors exhaust
        all retries, automatically falls back to the fallback model.

        Args:
            prompt: Full prompt string.

        Returns:
            Generated text from the model.

        Raises:
            GeminiError: On unrecoverable API errors or exhausted retries on
                both primary and fallback models.
        """
        try:
            return self._generate_with_retries(self.model, prompt)
        except GeminiError as primary_exc:
            err_lower = str(primary_exc).lower()
            use_fallback = (
                self.fallback_model
                and self.fallback_model != self.model
                and any(k in err_lower for k in _QUOTA_KEYWORDS)
            )
            if use_fallback:
                logger.warning(
                    "Primary model %s quota/rate-limit exhausted. "
                    "Switching to fallback model %s...",
                    self.model,
                    self.fallback_model,
                )
                return self._generate_with_retries(self.fallback_model, prompt)
            raise
