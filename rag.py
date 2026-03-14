"""
RAG engine — JSON-backed document store with hybrid retrieval.

Retrieval pipeline:
  1. Keyword scoring  (BM25-style TF-IDF)
  2. Semantic scoring  (OpenAI embeddings + cosine similarity)
  3. Fused ranking     (weighted combination)
  4. Relevance gate    (drop below threshold)
  5. Optional metadata filter
  6. Smart chunking on ingest (overlap-aware splitting)
"""

import json
import math
import os
import re
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Optional

import numpy as np
from langchain_openai import OpenAIEmbeddings
from pydantic import BaseModel, Field


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


def _smart_chunk(text: str, max_tokens: int = 300, overlap: int = 60) -> list[str]:
    """Split text into overlapping chunks respecting sentence boundaries."""
    sentences = re.split(r'(?<=[.!?।\n])\s+', text.strip())
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for sent in sentences:
        sent_len = len(sent.split())
        if current_len + sent_len > max_tokens and current:
            chunks.append(" ".join(current))
            # Keep last N tokens worth of sentences for overlap
            overlap_buf: list[str] = []
            overlap_len = 0
            for s in reversed(current):
                slen = len(s.split())
                if overlap_len + slen > overlap:
                    break
                overlap_buf.insert(0, s)
                overlap_len += slen
            current = overlap_buf
            current_len = overlap_len
        current.append(sent)
        current_len += sent_len

    if current:
        chunks.append(" ".join(current))
    return chunks if chunks else [text]


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _bm25_score(query_tokens: list[str], doc_counts: dict[str, int], doc_len: int,
                avg_dl: float, df: dict[str, int], n_docs: int,
                k1: float = 1.5, b: float = 0.75) -> float:
    score = 0.0
    for qt in query_tokens:
        if qt not in doc_counts:
            continue
        tf = doc_counts[qt]
        d = df.get(qt, 0)
        idf = math.log((n_docs - d + 0.5) / (d + 0.5) + 1.0)
        tf_norm = (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * doc_len / max(avg_dl, 1)))
        score += idf * tf_norm
    return score


# ---------------------------------------------------------------------------
# RAG Store
# ---------------------------------------------------------------------------

DEFAULT_JSON_PATH = Path(__file__).parent / "data" / "documents.json"

# Weights for hybrid fusion
SEMANTIC_WEIGHT = 0.65
KEYWORD_WEIGHT = 0.35
DEFAULT_RELEVANCE_THRESHOLD = 0.25


class RAGStore:
    """Hybrid (semantic + keyword) retrieval over a JSON document store."""

    def __init__(
        self,
        json_path: Optional[Path] = None,
        embedding_model: str = "text-embedding-3-small",
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

        # Corpus-level BM25 stats (rebuilt on load / add)
        self._df: dict[str, int] = {}
        self._avg_dl: float = 0.0

    # ----- embedding helper -----

    def _get_embeddings(self) -> OpenAIEmbeddings:
        if self._embeddings is None:
            self._embeddings = OpenAIEmbeddings(model=self.embedding_model)
        return self._embeddings

    # ----- BM25 index -----

    def _rebuild_bm25_index(self) -> None:
        df: dict[str, int] = {}
        total_len = 0
        for c in self._chunks:
            if c.token_counts is None:
                c.token_counts = dict(Counter(_tokenize(c.text)))
            total_len += sum(c.token_counts.values())
            for token in c.token_counts:
                df[token] = df.get(token, 0) + 1
        self._df = df
        self._avg_dl = total_len / max(len(self._chunks), 1)

    # ----- persistence -----

    def load(self) -> None:
        if not self.json_path.exists():
            self._chunks = []
            return

        with open(self.json_path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        self._chunks = [DocumentChunk(**item) for item in raw]

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
        chunk_size: int = 300,
        chunk_overlap: int = 60,
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
            if auto_chunk and len(text.split()) > chunk_size:
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
        Hybrid retrieval.
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
        n_docs = len(candidates)

        # --- keyword scores ---
        keyword_scores: list[float] = []
        for c in candidates:
            if c.token_counts is None:
                c.token_counts = dict(Counter(_tokenize(c.text)))
            doc_len = sum(c.token_counts.values())
            keyword_scores.append(
                _bm25_score(query_tokens, c.token_counts, doc_len, self._avg_dl, self._df, n_docs)
            )

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

        # --- normalise into [0,1] ---
        def _minmax(arr: list[float]) -> list[float]:
            lo, hi = min(arr), max(arr)
            rng = hi - lo
            if rng == 0:
                return [0.0] * len(arr)
            return [(v - lo) / rng for v in arr]

        norm_kw = _minmax(keyword_scores)
        norm_sem = _minmax(semantic_scores)

        fused = [
            SEMANTIC_WEIGHT * s + KEYWORD_WEIGHT * kw
            for s, kw in zip(norm_sem, norm_kw)
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
