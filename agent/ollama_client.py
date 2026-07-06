"""Ollama client module.

Handles communication with a locally running Ollama instance: loads prompt
templates, fills placeholders, and produces code review output using any
model installed in Ollama (e.g. qwen2.5:7b).
"""

from __future__ import annotations

import logging

import ollama

from agent.base_client import BaseLLMClient, LLMError
from agent.config_manager import get_review_config

logger = logging.getLogger(__name__)


class OllamaError(LLMError):
    """Exception raised for Ollama API errors."""


class OllamaClient(BaseLLMClient):
    """Code review client backed by a local Ollama instance."""

    def __init__(self) -> None:
        cfg = get_review_config()
        super().__init__(
            max_tokens=cfg.get("max_tokens", 2048),
            temperature=cfg.get("temperature", 0.3),
        )
        self.model = cfg.get("ollama_model", "qwen2.5:7b")
        base_url = cfg.get("ollama_base_url", "http://localhost:11434")
        self._client = ollama.Client(host=base_url)
        logger.info("OllamaClient initialized — model: %s, host: %s", self.model, base_url)

    def _call_api(self, prompt: str) -> str:
        """Sends a prompt to the local Ollama instance with retry logic.

        Args:
            prompt: Full prompt string.

        Returns:
            Generated text from the model.

        Raises:
            OllamaError: On connection errors or unexpected API responses
                after all retry attempts.
        """
        import time

        last_exception: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                response = self._client.chat(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    options={
                        "num_predict": self.max_tokens,
                        "temperature": self.temperature,
                    },
                )
                self.usage_stats["prompt_tokens"] += response.get("prompt_eval_count", 0)
                self.usage_stats["completion_tokens"] += response.get("eval_count", 0)
                return response["message"]["content"]
            except OllamaError:
                raise
            except Exception as exc:
                last_exception = exc
                if attempt < self.max_retries:
                    wait = self.initial_backoff * (2 ** (attempt - 1))
                    logger.warning(
                        "Ollama error (attempt %d/%d): %s — retrying in %ds...",
                        attempt,
                        self.max_retries,
                        exc,
                        wait,
                    )
                    time.sleep(wait)
                    continue
                raise OllamaError(
                    f"Ollama API error after {self.max_retries} attempts: {exc}"
                ) from exc

        raise OllamaError(
            f"No response from Ollama after {self.max_retries} attempts: {last_exception}"
        )
