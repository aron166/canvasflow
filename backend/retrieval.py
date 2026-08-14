"""
Context assembly — decides how a source reaches the model.

The rule, in order:

  1. If the source fits the engine's context budget, send it **whole**. Nothing is lost.
     Modern long-context models make this the common case, and it beats retrieval on
     quality every time.
  2. Only when it doesn't fit, chunk it and retrieve the passages most relevant to the
     downstream node's instruction, with citation labels attached.

Summarizing is deliberately not a step: it discards the specific number or quote that
the retrieval was for.

Retrieval scoring uses Ollama embeddings when an embedding model is available, and falls
back to BM25 keyword scoring otherwise. BM25 is meaningfully worse at paraphrase ("what
did they say about pricing" vs a passage saying "costs $40/mo"), so the API surfaces
which one ran and the UI says so.

ponytail: brute-force cosine over every chunk, no vector index. At canvas scale (a few
thousand chunks) that is sub-millisecond in numpy. Add a real index only if a board ever
holds hundreds of long sources.
"""

from __future__ import annotations

import math
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import httpx
import numpy as np

DB_PATH = Path(__file__).parent / ".canvasflow" / "sources.db"
EMBED_MODEL = "nomic-embed-text"
EMBED_BATCH = 32

# Characters, not tokens — a deliberate approximation. ~4 chars/token holds well enough
# for English prose, and the alternative is a tokenizer dependency per provider.
CONTEXT_BUDGETS: dict[str, int] = {
    "anthropic_api": 600_000,  # 1M-token window, kept well clear of the ceiling
    "claude_cli": 300_000,
    "ollama": 24_000,  # local models are typically 8k-32k tokens
}
DEFAULT_BUDGET = 24_000

CHUNK_CHARS = 1_800
CHUNK_OVERLAP = 250


@dataclass
class Chunk:
    source_id: str
    index: int
    text: str
    label: str


@dataclass
class RetrievalOutcome:
    """What the executor sends downstream, plus how it was assembled — the UI shows this
    so a truncated or retrieved context is never silent."""

    text: str
    strategy: str  # "whole" | "embeddings" | "keyword"
    used_chars: int
    total_chars: int
    chunks_used: int
    chunks_total: int
    note: str


# --------------------------------------------------------------------------------------
# Chunking
# --------------------------------------------------------------------------------------


def chunk_text(text: str, source_id: str) -> list[Chunk]:
    """Splits on paragraph boundaries, packing up to CHUNK_CHARS with overlap.

    Splitting mid-sentence is what makes retrieved context read as nonsense, so we only
    break inside a paragraph when a single paragraph is itself oversized.
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        paragraphs = [text.strip()]

    chunks: list[Chunk] = []
    buffer: list[str] = []
    size = 0

    def flush() -> None:
        nonlocal buffer, size
        if not buffer:
            return
        body = "\n\n".join(buffer)
        chunks.append(
            Chunk(
                source_id=source_id,
                index=len(chunks),
                text=body,
                label=_infer_label(body) or f"part {len(chunks) + 1}",
            )
        )
        # Carry the tail forward so a fact spanning a boundary survives in one chunk.
        tail = body[-CHUNK_OVERLAP:] if len(body) > CHUNK_OVERLAP else ""
        buffer = [tail] if tail else []
        size = len(tail)

    for paragraph in paragraphs:
        if len(paragraph) > CHUNK_CHARS:
            flush()
            for start in range(0, len(paragraph), CHUNK_CHARS - CHUNK_OVERLAP):
                piece = paragraph[start : start + CHUNK_CHARS]
                chunks.append(
                    Chunk(
                        source_id=source_id,
                        index=len(chunks),
                        text=piece,
                        label=_infer_label(piece) or f"part {len(chunks) + 1}",
                    )
                )
            continue

        if size + len(paragraph) > CHUNK_CHARS:
            flush()
        buffer.append(paragraph)
        size += len(paragraph) + 2

    flush()
    return chunks


def _infer_label(body: str) -> str | None:
    """Recovers the `[12:04]` / `[p. 7]` marker the ingest layer inlined, so a retrieved
    chunk can still be cited."""
    match = re.search(r"\[((?:\d+:)?\d+:\d{2}|p\. \d+)\]", body)
    return match.group(1) if match else None


# --------------------------------------------------------------------------------------
# Embeddings (optional — Ollama)
# --------------------------------------------------------------------------------------


async def embeddings_available(ollama_url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{ollama_url.rstrip('/')}/api/tags")
            response.raise_for_status()
            names = [m.get("name", "") for m in response.json().get("models", [])]
    except Exception:  # noqa: BLE001 - availability check must never raise
        return False
    return any(name.split(":")[0] == EMBED_MODEL for name in names)


async def embed_texts(texts: list[str], ollama_url: str) -> np.ndarray | None:
    """Returns an (n, dim) L2-normalised matrix, or None if embedding is unavailable."""
    if not texts:
        return None

    vectors: list[list[float]] = []
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            for start in range(0, len(texts), EMBED_BATCH):
                batch = texts[start : start + EMBED_BATCH]
                response = await client.post(
                    f"{ollama_url.rstrip('/')}/api/embed",
                    json={"model": EMBED_MODEL, "input": batch},
                )
                if response.status_code != 200:
                    return None
                payload = response.json().get("embeddings")
                if not payload or len(payload) != len(batch):
                    return None
                vectors.extend(payload)
    except Exception:  # noqa: BLE001 - fall back to keyword scoring
        return None

    matrix = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0  # a zero vector would produce NaN on divide
    return matrix / norms


# --------------------------------------------------------------------------------------
# BM25 fallback
# --------------------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    "the a an and or but if of to in on for with at by from is are was were be been it "
    "this that these those as not no do does did will would can could should i you he "
    "she they we what which who when where why how".split()
)


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS and len(t) > 1]


def bm25_scores(query: str, documents: list[str], k1: float = 1.5, b: float = 0.75) -> np.ndarray:
    """Standard BM25. Used when no embedding model is installed."""
    tokenized = [_tokenize(doc) for doc in documents]
    lengths = np.array([len(doc) or 1 for doc in tokenized], dtype=np.float32)
    avg_length = float(lengths.mean()) if len(lengths) else 1.0
    total_docs = len(documents)

    document_frequency = Counter()
    for tokens in tokenized:
        document_frequency.update(set(tokens))

    scores = np.zeros(total_docs, dtype=np.float32)
    for term in set(_tokenize(query)):
        containing = document_frequency.get(term, 0)
        if containing == 0:
            continue
        idf = math.log(1 + (total_docs - containing + 0.5) / (containing + 0.5))
        for index, tokens in enumerate(tokenized):
            frequency = tokens.count(term)
            if frequency == 0:
                continue
            denominator = frequency + k1 * (1 - b + b * lengths[index] / avg_length)
            scores[index] += idf * (frequency * (k1 + 1)) / denominator
    return scores


# --------------------------------------------------------------------------------------
# Embedding cache
# --------------------------------------------------------------------------------------


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS chunk_vectors (
            source_id  TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            vector     BLOB NOT NULL,
            dim        INTEGER NOT NULL,
            PRIMARY KEY (source_id, chunk_index)
        )
        """
    )
    return connection


def load_vectors(source_id: str, expected: int) -> np.ndarray | None:
    """Returns the cached matrix only if it matches the current chunk count — a source
    that was re-ingested must not be scored against stale vectors."""
    try:
        with _connect() as connection:
            rows = connection.execute(
                "SELECT chunk_index, vector, dim FROM chunk_vectors "
                "WHERE source_id = ? ORDER BY chunk_index",
                (source_id,),
            ).fetchall()
    except sqlite3.Error:
        return None

    if len(rows) != expected or not rows:
        return None
    dim = rows[0][2]
    return np.stack([np.frombuffer(row[1], dtype=np.float32).reshape(dim) for row in rows])


def store_vectors(source_id: str, matrix: np.ndarray) -> None:
    try:
        with _connect() as connection:
            connection.execute("DELETE FROM chunk_vectors WHERE source_id = ?", (source_id,))
            connection.executemany(
                "INSERT INTO chunk_vectors (source_id, chunk_index, vector, dim) "
                "VALUES (?, ?, ?, ?)",
                [
                    (source_id, index, row.astype(np.float32).tobytes(), int(row.shape[0]))
                    for index, row in enumerate(matrix)
                ],
            )
    except sqlite3.Error:
        pass  # cache miss next time is the only cost


def forget_source(source_id: str) -> None:
    try:
        with _connect() as connection:
            connection.execute("DELETE FROM chunk_vectors WHERE source_id = ?", (source_id,))
    except sqlite3.Error:
        pass


# --------------------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------------------


def context_budget(provider: str) -> int:
    return CONTEXT_BUDGETS.get(provider, DEFAULT_BUDGET)


def plan_windows(text: str, budget_chars: int, source_id: str = "w") -> list[str]:
    """Splits an oversized source into sequential windows that each fit the budget.

    Used by full-coverage mode, where the point is that *every* part of the source is
    read — so windows are contiguous and ordered, unlike retrieval which cherry-picks.
    Chunk-level overlap carries context across the seams.
    """
    if len(text) <= budget_chars:
        return [text]

    chunks = chunk_text(text, source_id)
    windows: list[str] = []
    current: list[str] = []
    size = 0

    for chunk in chunks:
        piece = f"[{chunk.label}]\n{chunk.text}"
        # A single chunk larger than the budget gets its own window and is hard-cut;
        # CHUNK_CHARS is well under any real budget, so this is a guard, not a path.
        if size + len(piece) > budget_chars and current:
            windows.append("\n\n".join(current))
            current, size = [], 0
        current.append(piece)
        size += len(piece) + 2

    if current:
        windows.append("\n\n".join(current))
    return windows


async def assemble_context(
    *,
    source_id: str,
    title: str,
    text: str,
    query: str,
    budget_chars: int,
    ollama_url: str,
) -> RetrievalOutcome:
    """Fits `text` into `budget_chars`, keeping as much signal as possible."""

    total = len(text)

    # 1. Whole source — the good case.
    if total <= budget_chars:
        return RetrievalOutcome(
            text=text,
            strategy="whole",
            used_chars=total,
            total_chars=total,
            chunks_used=0,
            chunks_total=0,
            note="Full source sent — nothing omitted.",
        )

    # 2. Too big. Chunk and score.
    chunks = chunk_text(text, source_id)
    if not chunks:
        return RetrievalOutcome(
            text=text[:budget_chars],
            strategy="whole",
            used_chars=budget_chars,
            total_chars=total,
            chunks_used=0,
            chunks_total=0,
            note="Source truncated to fit the context budget.",
        )

    bodies = [chunk.text for chunk in chunks]
    strategy = "keyword"
    scores: np.ndarray | None = None

    if await embeddings_available(ollama_url):
        matrix = load_vectors(source_id, len(chunks))
        if matrix is None:
            matrix = await embed_texts(bodies, ollama_url)
            if matrix is not None:
                store_vectors(source_id, matrix)
        query_vector = await embed_texts([query or title], ollama_url)
        if matrix is not None and query_vector is not None and matrix.shape[1] == query_vector.shape[1]:
            scores = matrix @ query_vector[0]  # both are unit-norm, so this is cosine
            strategy = "embeddings"

    if scores is None:
        scores = bm25_scores(query or title, bodies)

    # Take the highest-scoring chunks that fit, then restore document order so the
    # model reads them in the sequence they were written.
    ranked = sorted(range(len(chunks)), key=lambda i: float(scores[i]), reverse=True)
    selected: list[int] = []
    used = 0
    for index in ranked:
        size = len(chunks[index].text) + 2
        if used + size > budget_chars:
            continue
        selected.append(index)
        used += size
    selected.sort()

    if not selected:  # a single chunk larger than the whole budget
        selected = [ranked[0]]
        used = budget_chars

    body = "\n\n".join(f"[{chunks[i].label}]\n{chunks[i].text}" for i in selected)
    method = "semantic similarity" if strategy == "embeddings" else "keyword match"
    note = (
        f"Source is {total:,} chars — too large to send whole. Selected the "
        f"{len(selected)} of {len(chunks)} passages most relevant to this node's "
        f"instruction, by {method}."
    )

    return RetrievalOutcome(
        text=body,
        strategy=strategy,
        used_chars=len(body),
        total_chars=total,
        chunks_used=len(selected),
        chunks_total=len(chunks),
        note=note,
    )


# --------------------------------------------------------------------------------------
# Self-check:  python retrieval.py
# --------------------------------------------------------------------------------------


def _demo() -> None:
    # Chunking respects the size ceiling and preserves all content.
    paragraphs = [f"Paragraph {i}. " + ("filler words here. " * 40) for i in range(20)]
    text = "\n\n".join(paragraphs)
    chunks = chunk_text(text, "s1")
    assert len(chunks) > 1, "long text must split"
    assert all(len(c.text) <= CHUNK_CHARS + CHUNK_OVERLAP for c in chunks)
    assert "Paragraph 19" in chunks[-1].text

    # A single oversized paragraph is force-split rather than dropped.
    giant = chunk_text("x" * (CHUNK_CHARS * 3), "s2")
    assert len(giant) >= 3

    # Timestamp and page markers survive into chunk labels.
    labelled = chunk_text("[12:04] they said pricing is the blocker", "s3")
    assert labelled[0].label == "12:04"
    assert chunk_text("[p. 7]\nrevenue grew", "s4")[0].label == "p. 7"

    # BM25 ranks the passage that actually answers the query first.
    docs = [
        "the weather in spring is mild and pleasant",
        "quarterly revenue grew 40 percent driven by subscription pricing",
        "our office moved to a new building downtown",
    ]
    assert int(np.argmax(bm25_scores("revenue pricing growth", docs))) == 1

    # A query with no overlap must not crash or fabricate a winner.
    assert bm25_scores("zzz qqq", docs).max() == 0.0

    print("retrieval self-check passed")


if __name__ == "__main__":
    _demo()
