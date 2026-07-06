"""Vector store — ChromaDB-based document storage and retrieval.

Provides CRUD operations for corporate knowledge documents and semantic
search capabilities for RAG-powered review context enrichment.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default persistent storage directory
_DEFAULT_PERSIST_DIR = Path(__file__).parent.parent.parent / ".data" / "chromadb"
_COLLECTION_NAME = "corporate_knowledge"


class VectorStoreError(Exception):
    """Exception raised for vector store errors."""


class VectorStore:
    """ChromaDB-based vector store for corporate knowledge documents.

    Stores and retrieves documents using semantic similarity search.
    Documents can be categorized by type (coding standards, past PRs,
    architecture docs, etc.).

    Attributes:
        collection_name: Name of the ChromaDB collection.
    """

    def __init__(
        self,
        persist_directory: str | Path | None = None,
        collection_name: str = _COLLECTION_NAME,
        embedding_provider: Any = None,
    ) -> None:
        """Initializes the vector store.

        Args:
            persist_directory: Path for ChromaDB persistent storage.
            collection_name: Name of the collection to use.
            embedding_provider: Embedding provider instance. If None,
                uses the default provider.
        """
        try:
            import chromadb
        except ImportError:
            raise ImportError(
                "chromadb is required for VectorStore. Install with: pip install chromadb"
            )

        self.collection_name = collection_name
        persist_path = Path(persist_directory or _DEFAULT_PERSIST_DIR)
        persist_path.mkdir(parents=True, exist_ok=True)

        self._client = chromadb.PersistentClient(path=str(persist_path))

        # Set up embedding function
        if embedding_provider is not None:
            self._embedding_fn = _ChromaEmbeddingAdapter(embedding_provider)
            self._collection = self._client.get_or_create_collection(
                name=collection_name,
                embedding_function=self._embedding_fn,
            )
        else:
            # Use ChromaDB's default embedding
            self._collection = self._client.get_or_create_collection(
                name=collection_name,
            )

        logger.info(
            "VectorStore initialized — collection: '%s', docs: %d",
            collection_name,
            self._collection.count(),
        )

    def add_document(
        self,
        content: str,
        doc_type: str = "general",
        metadata: dict[str, str] | None = None,
        doc_id: str | None = None,
    ) -> str:
        """Adds a document to the vector store.

        Args:
            content: Document text content.
            doc_type: Category type (e.g. ``coding_standards``,
                ``approved_pr``, ``architecture``, ``instructions``).
            metadata: Optional additional metadata.
            doc_id: Optional document ID. Auto-generated if not provided.

        Returns:
            Document ID.

        Raises:
            VectorStoreError: If the document cannot be added.
        """
        if doc_id is None:
            doc_id = str(uuid.uuid4())

        meta = {"type": doc_type}
        if metadata:
            meta.update(metadata)

        try:
            self._collection.add(
                ids=[doc_id],
                documents=[content],
                metadatas=[meta],
            )
            logger.info("Document added: id=%s, type=%s", doc_id[:8], doc_type)
            return doc_id
        except Exception as exc:
            raise VectorStoreError(f"Failed to add document: {exc}") from exc

    def add_documents(
        self,
        contents: list[str],
        doc_type: str = "general",
        metadatas: list[dict[str, str]] | None = None,
    ) -> list[str]:
        """Adds multiple documents to the vector store.

        Args:
            contents: List of document text contents.
            doc_type: Category type for all documents.
            metadatas: Optional per-document metadata.

        Returns:
            List of document IDs.

        Raises:
            VectorStoreError: If the documents cannot be added.
        """
        ids = [str(uuid.uuid4()) for _ in contents]
        metas = []
        for i, content in enumerate(contents):
            meta = {"type": doc_type}
            if metadatas and i < len(metadatas):
                meta.update(metadatas[i])
            metas.append(meta)

        try:
            self._collection.add(
                ids=ids,
                documents=contents,
                metadatas=metas,
            )
            logger.info("Added %d documents of type '%s'.", len(contents), doc_type)
            return ids
        except Exception as exc:
            raise VectorStoreError(f"Failed to add documents: {exc}") from exc

    def search(
        self,
        query: str,
        n_results: int = 5,
        doc_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Searches for similar documents.

        Args:
            query: Search query text.
            n_results: Maximum number of results to return.
            doc_type: Optional filter by document type.

        Returns:
            List of result dictionaries with ``id``, ``content``,
            ``metadata``, and ``distance`` keys.
        """
        where_filter = {"type": doc_type} if doc_type else None

        try:
            results = self._collection.query(
                query_texts=[query],
                n_results=n_results,
                where=where_filter,
            )
        except Exception as exc:
            logger.error("Search failed: %s", exc)
            return []

        documents: list[dict[str, Any]] = []
        if results and results["ids"]:
            for i, doc_id in enumerate(results["ids"][0]):
                documents.append(
                    {
                        "id": doc_id,
                        "content": results["documents"][0][i] if results["documents"] else "",
                        "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                        "distance": results["distances"][0][i] if results["distances"] else 0.0,
                    }
                )

        return documents

    def delete_document(self, doc_id: str) -> None:
        """Deletes a document from the vector store.

        Args:
            doc_id: Document ID to delete.

        Raises:
            VectorStoreError: If the document cannot be deleted.
        """
        try:
            self._collection.delete(ids=[doc_id])
            logger.info("Document deleted: %s", doc_id[:8])
        except Exception as exc:
            raise VectorStoreError(f"Failed to delete document: {exc}") from exc

    def list_documents(
        self,
        doc_type: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Lists documents in the vector store.

        Args:
            doc_type: Optional filter by document type.
            limit: Maximum number of documents to return.

        Returns:
            List of document dictionaries with ``id``, ``content``,
            and ``metadata`` keys.
        """
        where_filter = {"type": doc_type} if doc_type else None

        try:
            results = self._collection.get(
                where=where_filter,
                limit=limit,
            )
        except Exception as exc:
            logger.error("List failed: %s", exc)
            return []

        documents: list[dict[str, Any]] = []
        if results and results["ids"]:
            for i, doc_id in enumerate(results["ids"]):
                documents.append(
                    {
                        "id": doc_id,
                        "content": results["documents"][i] if results["documents"] else "",
                        "metadata": results["metadatas"][i] if results["metadatas"] else {},
                    }
                )

        return documents

    def count(self) -> int:
        """Returns the total number of documents in the store."""
        return self._collection.count()

    def clear(self) -> None:
        """Deletes all documents from the collection."""
        try:
            self._client.delete_collection(self.collection_name)
            self._collection = self._client.get_or_create_collection(
                name=self.collection_name,
            )
            logger.info("Collection '%s' cleared.", self.collection_name)
        except Exception as exc:
            raise VectorStoreError(f"Failed to clear collection: {exc}") from exc


class _ChromaEmbeddingAdapter:
    """Adapts our embedding provider to ChromaDB's embedding function interface."""

    def __init__(self, provider: Any) -> None:
        self._provider = provider

    def __call__(self, input: list[str]) -> list[list[float]]:
        """Generates embeddings for ChromaDB.

        Args:
            input: List of texts to embed.

        Returns:
            List of embedding vectors.
        """
        return self._provider.embed_batch(input)
