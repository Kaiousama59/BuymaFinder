from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from buymafinder.core.models import Product, Source
from buymafinder.core.product_serialization import product_from_json, product_to_json


SCHEMA = """
CREATE TABLE IF NOT EXISTS scan_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS scan_sources (
    run_id INTEGER NOT NULL REFERENCES scan_runs(id),
    shop_code TEXT NOT NULL,
    target TEXT NOT NULL,
    category TEXT NOT NULL,
    list_url TEXT NOT NULL,
    status TEXT NOT NULL,
    product_count INTEGER,
    error TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, list_url)
);
CREATE TABLE IF NOT EXISTS scan_products (
    run_id INTEGER NOT NULL REFERENCES scan_runs(id),
    product_url TEXT NOT NULL,
    sku TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    PRIMARY KEY (run_id, product_url)
);
"""


class ScanStateRepository:
    """Persist per-source scan progress so interrupted runs can be resumed."""

    def __init__(self, database_path: Path) -> None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(database_path)
        self._connection.executescript(SCHEMA)
        self._connection.commit()
        self.run_id: int | None = None

    def start_run(self, resume: bool = False) -> int:
        """Create a new run, or continue the latest unfinished run when resuming."""
        if resume:
            row = self._connection.execute(
                "SELECT id FROM scan_runs WHERE finished_at IS NULL ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row is not None:
                self.run_id = int(row[0])
                return self.run_id
        cursor = self._connection.execute(
            "INSERT INTO scan_runs (started_at) VALUES (?)", (_now(),)
        )
        self._connection.commit()
        self.run_id = int(cursor.lastrowid)
        return self.run_id

    def finish_run(self) -> None:
        """Mark the current run as finished."""
        self._require_run()
        self._connection.execute(
            "UPDATE scan_runs SET finished_at = ? WHERE id = ?", (_now(), self.run_id)
        )
        self._connection.commit()

    def should_skip(self, source: Source) -> bool:
        """Return True when the source already completed within the current run."""
        self._require_run()
        row = self._connection.execute(
            "SELECT status FROM scan_sources WHERE run_id = ? AND list_url = ?",
            (self.run_id, source.list_url),
        ).fetchone()
        return row is not None and row[0] == "completed"

    def record_success(self, source: Source, product_count: int) -> None:
        """Record a successfully collected source with its product count."""
        self._record(source, "completed", product_count, "")

    def record_failure(self, source: Source, error: str) -> None:
        """Record a failed source so a resumed run retries it."""
        self._record(source, "failed", None, error)

    def save_product(self, product: Product) -> None:
        """Persist a collected product so an interrupted run can be resumed."""
        self._require_run()
        self._connection.execute(
            """
            INSERT INTO scan_products (run_id, product_url, sku, payload, collected_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (run_id, product_url) DO UPDATE SET
                sku = excluded.sku,
                payload = excluded.payload,
                collected_at = excluded.collected_at
            """,
            (
                self.run_id,
                product.product_url,
                product.sku,
                product_to_json(product),
                _now(),
            ),
        )
        self._connection.commit()

    def load_products(self) -> list[Product]:
        """Load products already collected within the current run."""
        self._require_run()
        rows = self._connection.execute(
            "SELECT payload FROM scan_products WHERE run_id = ? ORDER BY collected_at",
            (self.run_id,),
        ).fetchall()
        return [product_from_json(row[0]) for row in rows]

    def close(self) -> None:
        self._connection.close()

    def _record(self, source: Source, status: str, product_count: int | None, error: str) -> None:
        self._require_run()
        self._connection.execute(
            """
            INSERT INTO scan_sources
                (run_id, shop_code, target, category, list_url, status, product_count, error, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (run_id, list_url) DO UPDATE SET
                status = excluded.status,
                product_count = excluded.product_count,
                error = excluded.error,
                updated_at = excluded.updated_at
            """,
            (
                self.run_id,
                source.shop_code,
                source.target,
                source.category,
                source.list_url,
                status,
                product_count,
                error,
                _now(),
            ),
        )
        self._connection.commit()

    def _require_run(self) -> None:
        if self.run_id is None:
            raise RuntimeError("Scan run has not been started; call start_run() first.")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
