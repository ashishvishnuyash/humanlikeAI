"""
RAG engine — JSON-backed document store with hybrid retrieval.

Retrieval pipeline:
  1. Keyword scoring  (BM25 via rank_bm25)
  2. Semantic scoring  (OpenAI embeddings + cosine similarity)
  3. Fused ranking     (Reciprocal Rank Fusion)
  4. Relevance gate    (drop below threshold)
  5. Optional metadata filter
  6. LangChain RecursiveCharacterTextSplitter on ingest
"""

import json
import os
import re
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Optional

import numpy as np
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pydantic import BaseModel, Field
from rank_bm25 import BM25Okapi


# ---------------------------------------------------------------------------
# Document schema
# ---------------------------------------------------------------------------

class DocumentChunk(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    text: str
    embedding: Optional[list[float]] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Stored for BM25 — rebuilt on load if missing
    token_counts: Optional[dict[str, int]] = None


# ---------------------------------------------------------------------------
# Text utilities
# ---------------------------------------------------------------------------

_STOP_WORDS = frozenset(
    "a an and are as at be but by for if in into is it no not of on or "
    "such that the their then there these they this to was will with".split()
)


def _tokenize(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9\u0900-\u097F\u0980-\u09FF]+", text.lower()) if w not in _STOP_WORDS]


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
# Scoring helpers
# ---------------------------------------------------------------------------

def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


# ---------------------------------------------------------------------------
# RAG Store
# ---------------------------------------------------------------------------

DEFAULT_JSON_PATH = Path(__file__).parent / "data" / "documents.json"

# RRF constant — higher value = more smoothing across rank differences
RRF_K = 60
DEFAULT_RELEVANCE_THRESHOLD = 0.15  # RRF max score ~0.033; 0.15 filters low-quality matches


class RAGStore:
    """Hybrid (semantic + BM25 keyword) retrieval over a JSON document store."""

    def __init__(
        self,
        json_path: Optional[Path] = None,
        embedding_model: str = "text-embedding-3-large",
        top_k: int = 5,
        relevance_threshold: float = DEFAULT_RELEVANCE_THRESHOLD,
    ):
        self.json_path = Path(json_path or DEFAULT_JSON_PATH)
        self.json_path.parent.mkdir(parents=True, exist_ok=True)
        self.embedding_model = embedding_model
        self.top_k = top_k
        self.relevance_threshold = relevance_threshold
        self._embeddings: Optional[OpenAIEmbeddings] = None
        self._chunks: list[DocumentChunk] = []

        # BM25 index — rebuilt whenever chunks change
        self._bm25: Optional[BM25Okapi] = None

    # ----- embedding helper -----

    def _get_embeddings(self) -> OpenAIEmbeddings:
        if self._embeddings is None:
            self._embeddings = OpenAIEmbeddings(model=self.embedding_model)
        return self._embeddings

    # ----- BM25 index -----

    def _rebuild_bm25_index(self) -> None:
        tokenized = []
        for c in self._chunks:
            if c.token_counts is None:
                c.token_counts = dict(Counter(_tokenize(c.text)))
            tokenized.append(list(c.token_counts.keys()))
        self._bm25 = BM25Okapi(tokenized) if tokenized else None

    # ----- persistence -----

    def load(self) -> None:
        if not self.json_path.exists():
            self._chunks = []
            return

        with open(self.json_path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        self._chunks = [DocumentChunk(**item) for item in raw]

        # Detect embedding dimension mismatch (e.g. switching small→large model) and re-embed
        if self._chunks and self._chunks[0].embedding:
            stored_dim = len(self._chunks[0].embedding)
            expected_dim = 3072 if "large" in self.embedding_model else 1536
            if stored_dim != expected_dim:
                for c in self._chunks:
                    c.embedding = None

        to_embed = [c for c in self._chunks if c.embedding is None]
        if to_embed and os.environ.get("OPENAI_API_KEY"):
            emb = self._get_embeddings()
            texts = [c.text for c in to_embed]
            vectors = emb.embed_documents(texts)
            for chunk, vec in zip(to_embed, vectors):
                chunk.embedding = vec
            self.save()

        self._rebuild_bm25_index()

    def save(self) -> None:
        data = [c.model_dump(mode="json") for c in self._chunks]
        with open(self.json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

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
        if not texts:
            return []

        meta_list = metadata_per_doc or [{}] * len(texts)
        all_chunks_text: list[str] = []
        all_meta: list[dict] = []

        for text, meta in zip(texts, meta_list):
            if auto_chunk and len(text) > chunk_size:
                parts = _smart_chunk(text, max_tokens=chunk_size, overlap=chunk_overlap)
            else:
                parts = [text]
            for part in parts:
                all_chunks_text.append(part)
                all_meta.append(meta)

        emb = self._get_embeddings()
        vectors = emb.embed_documents(all_chunks_text)

        ids = []
        for text, vec, meta in zip(all_chunks_text, vectors, all_meta):
            chunk = DocumentChunk(
                text=text,
                embedding=vec,
                metadata=meta,
                token_counts=dict(Counter(_tokenize(text))),
            )
            self._chunks.append(chunk)
            ids.append(chunk.id)

        self._rebuild_bm25_index()
        self.save()
        return ids

    # ----- hybrid retrieval -----

    def retrieve(
        self,
        query: str,
        top_k: Optional[int] = None,
        metadata_filter: Optional[dict[str, str]] = None,
        threshold: Optional[float] = None,
    ) -> list[dict[str, Any]]:
        """
        Hybrid retrieval using BM25 + semantic scores fused via Reciprocal Rank Fusion.
        Returns list of {"text": ..., "score": ..., "metadata": ...} dicts.
        """
        k = top_k or self.top_k
        gate = threshold if threshold is not None else self.relevance_threshold
        if not self._chunks:
            return []

        candidates = self._chunks
        if metadata_filter:
            candidates = [
                c for c in candidates
                if all(c.metadata.get(fk) == fv for fk, fv in metadata_filter.items())
            ]
        if not candidates:
            return []

        query_tokens = _tokenize(query)

        # --- keyword scores (BM25) ---
        if self._bm25 is not None and len(candidates) == len(self._chunks):
            # Full corpus: use pre-built index
            keyword_scores = self._bm25.get_scores(query_tokens).tolist()
        else:
            # Filtered subset: build a temporary BM25 index over candidates
            tokenized_candidates = []
            for c in candidates:
                if c.token_counts is None:
                    c.token_counts = dict(Counter(_tokenize(c.text)))
                tokenized_candidates.append(list(c.token_counts.keys()))
            tmp_bm25 = BM25Okapi(tokenized_candidates) if tokenized_candidates else None
            keyword_scores = tmp_bm25.get_scores(query_tokens).tolist() if tmp_bm25 else [0.0] * len(candidates)

        # --- semantic scores ---
        has_embeddings = any(c.embedding is not None for c in candidates)
        if has_embeddings and os.environ.get("OPENAI_API_KEY"):
            emb = self._get_embeddings()
            query_vec = np.array(emb.embed_query(query), dtype=np.float32)
            semantic_scores = []
            for c in candidates:
                if c.embedding is not None:
                    semantic_scores.append(_cosine_similarity(query_vec, np.array(c.embedding, dtype=np.float32)))
                else:
                    semantic_scores.append(0.0)
        else:
            semantic_scores = [0.0] * len(candidates)

        # --- Reciprocal Rank Fusion ---
        def _rrf_ranks(scores: list[float]) -> list[int]:
            order = sorted(range(len(scores)), key=lambda i: -scores[i])
            ranks = [0] * len(scores)
            for rank, idx in enumerate(order, start=1):
                ranks[idx] = rank
            return ranks

        sem_ranks = _rrf_ranks(semantic_scores)
        kw_ranks = _rrf_ranks(keyword_scores)

        fused = [
            1.0 / (RRF_K + sr) + 1.0 / (RRF_K + kr)
            for sr, kr in zip(sem_ranks, kw_ranks)
        ]

        ranked = sorted(
            zip(fused, candidates),
            key=lambda x: -x[0],
        )

        results = []
        for score, chunk in ranked[:k]:
            if score < gate:
                continue
            results.append({
                "text": chunk.text,
                "score": round(score, 4),
                "metadata": chunk.metadata,
            })

        return results

    def retrieve_texts(self, query: str, top_k: Optional[int] = None) -> list[str]:
        """Convenience: just the text strings."""
        return [r["text"] for r in self.retrieve(query, top_k=top_k)]

    # ----- CRUD helpers -----

    def list_chunks(self) -> list[dict[str, Any]]:
        return [
            {
                "id": c.id,
                "text": c.text[:200] + ("..." if len(c.text) > 200 else ""),
                "metadata": c.metadata,
                "has_embedding": c.embedding is not None,
            }
            for c in self._chunks
        ]

    def delete_chunk(self, chunk_id: str) -> bool:
        for i, c in enumerate(self._chunks):
            if c.id == chunk_id:
                self._chunks.pop(i)
                self._rebuild_bm25_index()
                self.save()
                return True
        return False

    @property
    def count(self) -> int:
        return len(self._chunks)


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
