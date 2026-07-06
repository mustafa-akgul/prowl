"""Embedding provider — flexible embedding interface for RAG.

Provides an abstract base class and concrete implementations for
generating text embeddings. Supports SentenceTransformers (default),
custom Turkish models, and a simple fallback using TF-IDF.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class BaseEmbeddingProvider(ABC):
    """Abstract base class for embedding providers.

    Subclasses must implement ``embed()`` and ``embed_batch()`` methods
    to convert text into vector representations.
    """

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """Generates an embedding vector for a single text.

        Args:
            text: Input text to embed.

        Returns:
            List of floats representing the embedding vector.
        """

    @abstractmethod
    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generates embedding vectors for multiple texts.

        Args:
            texts: List of input texts.

        Returns:
            List of embedding vectors.
        """

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Returns the dimensionality of the embedding vectors."""


class SentenceTransformerProvider(BaseEmbeddingProvider):
    """Embedding provider using SentenceTransformers.

    Supports any model from the HuggingFace model hub, including
    multilingual models suitable for Turkish text.

    Attributes:
        model_name: Name or path of the SentenceTransformer model.
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise ImportError(
                "sentence-transformers is required for SentenceTransformerProvider. "
                "Install with: pip install sentence-transformers"
            )

        self.model_name = model_name
        self._model = SentenceTransformer(model_name)
        self._dimension = self._model.get_sentence_embedding_dimension()
        logger.info("SentenceTransformer loaded: %s (dim=%d)", model_name, self._dimension)

    def embed(self, text: str) -> list[float]:
        """Generates an embedding for a single text.

        Args:
            text: Input text.

        Returns:
            Embedding vector as list of floats.
        """
        return self._model.encode(text).tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generates embeddings for a batch of texts.

        Args:
            texts: List of input texts.

        Returns:
            List of embedding vectors.
        """
        return self._model.encode(texts).tolist()

    @property
    def dimension(self) -> int:
        """Returns the embedding dimension."""
        return self._dimension


class DefaultEmbeddingProvider(BaseEmbeddingProvider):
    """Simple hash-based embedding provider as a zero-dependency fallback.

    Uses a deterministic hash function to produce fixed-dimension vectors.
    NOT suitable for semantic search — only for testing and situations
    where no ML library is available.
    """

    _DIM = 384

    def embed(self, text: str) -> list[float]:
        """Generates a deterministic pseudo-embedding.

        Args:
            text: Input text.

        Returns:
            Fixed-dimension float vector.
        """
        import hashlib

        h = hashlib.sha512(text.encode("utf-8")).hexdigest()
        # Split hash into chunks and convert to floats in [-1, 1]
        vec: list[float] = []
        for i in range(self._DIM):
            byte_val = int(h[(i * 2) % len(h) : (i * 2 + 2) % len(h) or len(h)], 16)
            vec.append((byte_val / 127.5) - 1.0)
        return vec

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generates pseudo-embeddings for a batch.

        Args:
            texts: List of input texts.

        Returns:
            List of float vectors.
        """
        return [self.embed(t) for t in texts]

    @property
    def dimension(self) -> int:
        """Returns the dimension (384)."""
        return self._DIM


def create_embedding_provider(
    provider_type: str = "default",
    model_name: str = "all-MiniLM-L6-v2",
) -> BaseEmbeddingProvider:
    """Factory function to create an embedding provider.

    Args:
        provider_type: Type of provider (``default``, ``sentence_transformer``).
        model_name: Model name for SentenceTransformer provider.

    Returns:
        An initialized embedding provider.
    """
    if provider_type == "sentence_transformer":
        return SentenceTransformerProvider(model_name=model_name)
    return DefaultEmbeddingProvider()
