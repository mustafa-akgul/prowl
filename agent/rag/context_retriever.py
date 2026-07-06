"""RAG context retriever — enriches prompts with corporate knowledge.

Retrieves relevant corporate knowledge from the vector store and
formats it for injection into LLM prompts.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class RAGContextRetriever:
    """Retrieves and formats corporate knowledge context for LLM prompts.

    Uses the vector store to find relevant coding standards, past PR
    reviews, architecture documents, and PM instructions based on the
    PR diff content.
    """

    def __init__(self, vector_store: Any = None) -> None:
        """Initializes the context retriever.

        Args:
            vector_store: VectorStore instance. If None, RAG is disabled
                and empty context is returned.
        """
        self._store = vector_store

    def get_context(
        self,
        query: str,
        n_results: int = 3,
        doc_types: list[str] | None = None,
    ) -> str:
        """Retrieves and formats relevant corporate knowledge.

        Args:
            query: Search query (typically the PR diff or summary).
            n_results: Maximum number of results per document type.
            doc_types: Specific document types to search. If None,
                searches all types.

        Returns:
            Formatted context string for prompt injection, or empty
            string if RAG is disabled or no results are found.
        """
        if self._store is None:
            return ""

        types_to_search = doc_types or [
            "coding_standards",
            "approved_pr",
            "architecture",
            "instructions",
            "general",
        ]

        all_results: list[dict[str, Any]] = []
        for doc_type in types_to_search:
            results = self._store.search(
                query=query[:2000],  # Truncate long diffs for search query
                n_results=n_results,
                doc_type=doc_type,
            )
            all_results.extend(results)

        if not all_results:
            return ""

        return self._format_context(all_results)

    @staticmethod
    def _format_context(results: list[dict[str, Any]]) -> str:
        """Formats RAG results into a structured context block.

        Args:
            results: List of search result dictionaries.

        Returns:
            Formatted context string.
        """
        parts: list[str] = ["**Corporate Knowledge Context (RAG):**"]

        type_labels = {
            "coding_standards": "📋 Coding Standard",
            "approved_pr": "✅ Approved PR Reference",
            "architecture": "🏗️ Architecture Document",
            "instructions": "📌 Team Instruction",
            "general": "📄 Reference Document",
        }

        for result in results:
            meta = result.get("metadata", {})
            doc_type = meta.get("type", "general")
            label = type_labels.get(doc_type, f"📄 {doc_type}")
            title = meta.get("title", "Untitled")
            content = result.get("content", "")[:500]

            parts.append(f"\n  [{label}] {title}")
            parts.append(f"  {content}")

        return "\n".join(parts)
