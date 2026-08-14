"""
Ingested-source store.

Ingestion is expensive — a 40-minute video transcription costs minutes of CPU — so a
resolved source is written to disk once and referenced by `source_id` from then on.
Nodes on the canvas carry only that id; the executor loads the text at run time.

SQLite for metadata, plain files for bodies: a 2-hour transcript is megabytes, and
keeping it out of the row means listing sources never pulls the text along with it.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ingest import ResolvedSource, Segment, SourceKind

STORE_DIR = Path(__file__).parent / ".canvasflow"
DB_PATH = STORE_DIR / "sources.db"
BODY_DIR = STORE_DIR / "bodies"
PREVIEW_CHARS = 1_200


class SourceStore:
    def __init__(self) -> None:
        BODY_DIR.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(DB_PATH)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sources (
                    source_id  TEXT PRIMARY KEY,
                    kind       TEXT NOT NULL,
                    title      TEXT NOT NULL,
                    origin     TEXT NOT NULL,
                    char_count INTEGER NOT NULL,
                    preview    TEXT NOT NULL,
                    meta       TEXT NOT NULL,
                    segments   TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )

    # -- writes -------------------------------------------------------------------

    def save(self, resolved: ResolvedSource) -> dict[str, Any]:
        source_id = f"src_{uuid.uuid4().hex[:16]}"
        (BODY_DIR / f"{source_id}.txt").write_text(resolved.text, encoding="utf-8")

        record = {
            "source_id": source_id,
            "kind": resolved.kind.value,
            "title": resolved.title[:300],
            "origin": resolved.origin[:2000],
            "char_count": resolved.char_count,
            "preview": resolved.text[:PREVIEW_CHARS],
            "meta": json.dumps(resolved.meta),
            # Segments are kept for future citation rendering; bodies live in the file.
            "segments": json.dumps([asdict(segment) for segment in resolved.segments[:5000]]),
        }

        with self._connect() as connection:
            connection.execute(
                "INSERT INTO sources (source_id, kind, title, origin, char_count, preview, "
                "meta, segments) VALUES (:source_id, :kind, :title, :origin, :char_count, "
                ":preview, :meta, :segments)",
                record,
            )

        return self.to_public(record)

    def delete(self, source_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM sources WHERE source_id = ?", (source_id,)
            )
            removed = cursor.rowcount > 0

        (BODY_DIR / f"{source_id}.txt").unlink(missing_ok=True)
        # Drop cached vectors too, or a recycled id would score against stale ones.
        retrieval_forget(source_id)
        return removed

    # -- reads --------------------------------------------------------------------

    def get(self, source_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sources WHERE source_id = ?", (source_id,)
            ).fetchone()
        return dict(row) if row else None

    def text(self, source_id: str) -> str | None:
        path = BODY_DIR / f"{source_id}.txt"
        return path.read_text(encoding="utf-8") if path.exists() else None

    def list_all(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM sources ORDER BY created_at DESC"
            ).fetchall()
        return [self.to_public(dict(row)) for row in rows]

    @staticmethod
    def to_public(record: dict[str, Any]) -> dict[str, Any]:
        """Shapes a row into the JSON the frontend's `IngestedSource` expects."""
        meta = record.get("meta")
        return {
            "source_id": record["source_id"],
            "kind": record["kind"],
            "title": record["title"],
            "origin": record["origin"],
            "char_count": record["char_count"],
            "preview": record["preview"],
            "meta": json.loads(meta) if isinstance(meta, str) else (meta or {}),
        }


def retrieval_forget(source_id: str) -> None:
    """Imported lazily — retrieval pulls in numpy, and the store is used in paths that
    don't otherwise need it."""
    try:
        from retrieval import forget_source

        forget_source(source_id)
    except Exception:  # noqa: BLE001 - cache cleanup must never fail a delete
        pass


# --------------------------------------------------------------------------------------
# Self-check:  python sources.py
# --------------------------------------------------------------------------------------


def _demo() -> None:
    store = SourceStore()
    resolved = ResolvedSource(
        kind=SourceKind.YOUTUBE,
        title="Test video",
        text="[0:00] hello world\n[0:05] second line",
        origin="https://youtu.be/abc",
        segments=[Segment(text="hello world", label="0:00", start=0.0)],
        meta={"duration_label": "0:05"},
    )

    public = store.save(resolved)
    assert public["source_id"].startswith("src_")
    assert public["kind"] == "youtube"
    assert public["char_count"] == len(resolved.text)
    assert public["meta"]["duration_label"] == "0:05"

    source_id = public["source_id"]
    assert store.text(source_id) == resolved.text
    assert store.get(source_id) is not None
    assert any(item["source_id"] == source_id for item in store.list_all())

    # Delete removes both the row and the body file.
    assert store.delete(source_id) is True
    assert store.get(source_id) is None
    assert store.text(source_id) is None
    assert store.delete(source_id) is False  # idempotent

    print("sources self-check passed")


if __name__ == "__main__":
    _demo()
