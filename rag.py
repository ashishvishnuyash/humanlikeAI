"""
RAG engine — Pinecone-backed document store with semantic retrieval.

Retrieval pipeline:
  1. Semantic scoring (OpenAI embeddings + cosine similarity)
  2. Relevance gate (drop below threshold)
  3. Optional metadata filter
  4. LangChain RecursiveCharacterTextSplitter on ingest
"""

import os
import uuid
import re
from typing import Any, Optional

from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_pinecone import PineconeVectorStore
from pinecone import Pinecone

# ---------------------------------------------------------------------------
# Text utilities
# ---------------------------------------------------------------------------

_text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=500,
    chunk_overlap=100,
    separators=["\n\n", "\n", ".", "!", "?", "।", " ", ""],
)


def _smart_chunk(text: str, max_tokens: int = 500, overlap: int = 100) -> list[str]:
    """Split text using LangChain RecursiveCharacterTextSplitter."""
    chunks = _text_splitter.split_text(text)
    return chunks if chunks else [text]


# ---------------------------------------------------------------------------
# RAG Store
# ---------------------------------------------------------------------------

# Use metric dependent threshold (Pinecone defaults to cosine similarity for texts: usually > 0.4 is decent)
DEFAULT_RELEVANCE_THRESHOLD = 0.4  


class RAGStore:
    """Semantic retrieval over a Pinecone document store."""

    def __init__(
        self,
        embedding_model: str = "text-embedding-3-large",
        top_k: int = 5,
        relevance_threshold: float = DEFAULT_RELEVANCE_THRESHOLD,
    ):
        self.embedding_model = embedding_model
        self.top_k = top_k
        self.relevance_threshold = relevance_threshold
        self._embeddings: Optional[OpenAIEmbeddings] = None
        
        self.index_name = os.environ.get("PINECONE_INDEX_NAME", "uma-rag")
        self.host = os.environ.get("PINECONE_HOST")  # optional direct host URL
        self._pc: Optional[Pinecone] = None
        self._vectorstore: Optional[PineconeVectorStore] = None

    # ----- embedding helper -----

    def _get_embeddings(self) -> OpenAIEmbeddings:
        if self._embeddings is None:
            if "text-embedding-3" in self.embedding_model:
                self._embeddings = OpenAIEmbeddings(model=self.embedding_model, dimensions=1024)
            else:
                self._embeddings = OpenAIEmbeddings(model=self.embedding_model)
        return self._embeddings

    # ----- persistence -----

    def load(self) -> None:
        """Initialize Pinecone client and vector store instance."""
        api_key = os.environ.get("PINECONE_API_KEY")
        if not api_key:
            print("WARNING: PINECONE_API_KEY is not set. RAGStore will be unavailable.")
            return

        target = f"host='{self.host}'" if self.host else f"index='{self.index_name}'"
        print(f"Connecting to Pinecone via {target}...")
        try:
            self._pc = Pinecone(api_key=api_key)
            if self.host:
                self._vectorstore = PineconeVectorStore(
                    host=self.host,
                    embedding=self._get_embeddings(),
                    pinecone_api_key=api_key,
                )
            else:
                self._vectorstore = PineconeVectorStore(
                    index_name=self.index_name,
                    embedding=self._get_embeddings(),
                    pinecone_api_key=api_key,
                )
            print("Successfully connected to Pinecone vector store.")
        except Exception as e:
            print(f"Error initializing Pinecone vector store: {e}")

    def save(self) -> None:
        """No-op as Pinecone persists automatically upon upsert."""
        pass

    # ----- ingest -----

    def add_documents(
        self,
        texts: list[str],
        metadata_per_doc: Optional[list[dict[str, Any]]] = None,
        auto_chunk: bool = True,
        chunk_size: int = 500,
        chunk_overlap: int = 100,
    ) -> list[str]:
        """
        Add texts.  If auto_chunk is True, long texts are split with overlap.
        Returns list of chunk IDs created.
        """
        if not texts or not self._vectorstore:
            return []

        meta_list = metadata_per_doc or [{}] * len(texts)
        all_chunks_text: list[str] = []
        all_meta: list[dict] = []
        all_ids: list[str] = []

        for text, meta in zip(texts, meta_list):
            if auto_chunk and len(text) > chunk_size:
                parts = _smart_chunk(text, max_tokens=chunk_size, overlap=chunk_overlap)
            else:
                parts = [text]
            for part in parts:
                all_chunks_text.append(part)
                # Per-chunk copy: langchain_pinecone writes the chunk text into
                # metadata['text'], so a shared dict ref would have every chunk
                # store the LAST chunk's text.
                all_meta.append(dict(meta))
                all_ids.append(str(uuid.uuid4()))

        # Add texts to Pinecone
        self._vectorstore.add_texts(
            texts=all_chunks_text,
            metadatas=all_meta,
            ids=all_ids
        )
        return all_ids

    # ----- retrieval -----

    def retrieve(
        self,
        query: str,
        top_k: Optional[int] = None,
        metadata_filter: Optional[dict[str, str]] = None,
        threshold: Optional[float] = None,
    ) -> list[dict[str, Any]]:
        """
        Semantic retrieval using Pinecone.
        Returns list of {"text": ..., "score": ..., "metadata": ...} dicts.
        """
        k = top_k or self.top_k
        gate = threshold if threshold is not None else self.relevance_threshold
        
        if not self._vectorstore:
            return []

        # Use similarity_search_with_score returns List[Tuple[Document, float]]
        results = []
        try:
            docs_and_scores = self._vectorstore.similarity_search_with_score(
                query=query,
                k=k,
                filter=metadata_filter
            )

            for doc, score in docs_and_scores:
                # Based on the distance metric used in Pinecone (cosine is common), 
                # you might need to interpret the score appropriately. 
                # Langchain similarity_search_with_score higher score is usually better for cosine.
                if score < gate:
                    continue
                results.append({
                    "text": doc.page_content,
                    "score": round(score, 4),
                    "metadata": doc.metadata,
                })
        except Exception as e:
            print(f"Retrieval error: {e}")

        return results

    def retrieve_texts(self, query: str, top_k: Optional[int] = None) -> list[str]:
        """Convenience: just the text strings."""
        return [r["text"] for r in self.retrieve(query, top_k=top_k)]

    # ----- CRUD helpers -----

    def list_chunks(self) -> list[dict[str, Any]]:
        """
        Listing chunks without query is not supported out-of-the-box by Pinecone 
        without pagination, ID fetching, or scanning. Usually we require a query.
        """
        return [{"text": "Listing features are disabled when using standard Pinecone", "metadata": {}, "has_embedding": True}]

    def delete_chunk(self, chunk_id: str) -> bool:
        """Deletes a chunk from Pinecone by chunk_id."""
        if not self._vectorstore:
            return False
            
        try:
            self._vectorstore.delete(ids=[chunk_id])
            return True
        except Exception as e:
            print(f"Error deleting chunk {chunk_id}: {e}")
            return False

    @property
    def count(self) -> int:
        """Returns the total number of vectors in the Pinecone index."""
        if self._pc:
            try:
                index = self._pc.Index(self.index_name)
                stats = index.describe_index_stats()
                return stats.get("total_vector_count", 0)
            except Exception:
                pass
        return 0


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_rag_store: Optional[RAGStore] = None


def get_rag_store() -> RAGStore:
    global _rag_store
    if _rag_store is None:
        _rag_store = RAGStore()
        _rag_store.load()
    return _rag_store
