from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, TypeVar

T = TypeVar("T")


@dataclass(slots=True)
class Summary:
    income: float
    expense: float
    count: int

    @property
    def profit(self) -> float:
        return self.income - self.expense


class Database:
    """SQLite storage with one short-lived connection per operation."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON;")
        return connection

    async def _run(self, function: Callable[[], T]) -> T:
        return await asyncio.to_thread(function)

    async def initialize(self) -> None:
        def operation() -> None:
            with self._connect() as db:
                db.execute("PRAGMA journal_mode=WAL;")
                db.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS transactions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        tx_type TEXT NOT NULL CHECK (tx_type IN ('income', 'expense')),
                        amount REAL NOT NULL CHECK (amount > 0),
                        category TEXT NOT NULL,
                        object_name TEXT,
                        comment TEXT,
                        created_at TEXT NOT NULL,
                        created_by INTEGER NOT NULL,
                        source TEXT NOT NULL DEFAULT 'manual',
                        deleted_at TEXT
                    );

                    CREATE INDEX IF NOT EXISTS idx_transactions_created_at
                        ON transactions(created_at);
                    CREATE INDEX IF NOT EXISTS idx_transactions_object
                        ON transactions(object_name);

                    CREATE TABLE IF NOT EXISTS audit_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        action TEXT NOT NULL,
                        entity_id INTEGER,
                        details TEXT,
                        created_at TEXT NOT NULL
                    );
                    """
                )

        await self._run(operation)

    async def add_transaction(
        self,
        *,
        tx_type: str,
        amount: float,
        category: str,
        object_name: str | None,
        comment: str | None,
        created_at: datetime,
        created_by: int,
        source: str,
    ) -> int:
        timestamp = created_at.strftime("%Y-%m-%d %H:%M:%S")

        def operation() -> int:
            with self._connect() as db:
                cursor = db.execute(
                    """
                    INSERT INTO transactions
                        (tx_type, amount, category, object_name, comment, created_at, created_by, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (tx_type, amount, category, object_name, comment, timestamp, created_by, source),
                )
                tx_id = int(cursor.lastrowid)
                db.execute(
                    """
                    INSERT INTO audit_log (user_id, action, entity_id, details, created_at)
                    VALUES (?, 'create_transaction', ?, ?, ?)
                    """,
                    (created_by, tx_id, f"{tx_type}:{amount}:{category}", timestamp),
                )
                return tx_id

        return await self._run(operation)

    async def summary(self, start: datetime | None = None, end: datetime | None = None) -> Summary:
        clauses = ["deleted_at IS NULL"]
        params: list[Any] = []
        if start:
            clauses.append("created_at >= ?")
            params.append(start.strftime("%Y-%m-%d %H:%M:%S"))
        if end:
            clauses.append("created_at < ?")
            params.append(end.strftime("%Y-%m-%d %H:%M:%S"))
        where = " AND ".join(clauses)

        def operation() -> Summary:
            with self._connect() as db:
                row = db.execute(
                    f"""
                    SELECT
                        COALESCE(SUM(CASE WHEN tx_type = 'income' THEN amount END), 0) AS income,
                        COALESCE(SUM(CASE WHEN tx_type = 'expense' THEN amount END), 0) AS expense,
                        COUNT(*) AS count
                    FROM transactions
                    WHERE {where}
                    """,
                    params,
                ).fetchone()
                return Summary(float(row["income"]), float(row["expense"]), int(row["count"]))

        return await self._run(operation)

    async def summary_by_object(self, object_query: str) -> tuple[Summary, list[dict[str, Any]]]:
        query = object_query.strip().casefold()

        def operation() -> tuple[Summary, list[dict[str, Any]]]:
            with self._connect() as db:
                source_rows = [
                    dict(item)
                    for item in db.execute(
                        """
                        SELECT id, tx_type, amount, category, object_name, comment, created_at
                        FROM transactions
                        WHERE deleted_at IS NULL AND object_name IS NOT NULL
                        ORDER BY created_at DESC, id DESC
                        """
                    ).fetchall()
                ]
                rows = [
                    row for row in source_rows
                    if query in str(row.get("object_name") or "").casefold()
                ]
                income = sum(float(row["amount"]) for row in rows if row["tx_type"] == "income")
                expense = sum(float(row["amount"]) for row in rows if row["tx_type"] == "expense")
                return Summary(income, expense, len(rows)), rows[:15]

        return await self._run(operation)

    async def object_totals(self) -> list[dict[str, Any]]:
        def operation() -> list[dict[str, Any]]:
            with self._connect() as db:
                return [
                    dict(item)
                    for item in db.execute(
                        """
                        SELECT object_name,
                               SUM(CASE WHEN tx_type = 'income' THEN amount ELSE 0 END) AS income,
                               SUM(CASE WHEN tx_type = 'expense' THEN amount ELSE 0 END) AS expense,
                               COUNT(*) AS count
                        FROM transactions
                        WHERE deleted_at IS NULL AND object_name IS NOT NULL AND TRIM(object_name) <> ''
                        GROUP BY object_name
                        ORDER BY MAX(created_at) DESC
                        LIMIT 100
                        """
                    ).fetchall()
                ]

        return await self._run(operation)

    async def recent(self, limit: int = 15) -> list[dict[str, Any]]:
        def operation() -> list[dict[str, Any]]:
            with self._connect() as db:
                return [
                    dict(item)
                    for item in db.execute(
                        """
                        SELECT id, tx_type, amount, category, object_name, comment, created_at, source
                        FROM transactions
                        WHERE deleted_at IS NULL
                        ORDER BY created_at DESC, id DESC
                        LIMIT ?
                        """,
                        (limit,),
                    ).fetchall()
                ]

        return await self._run(operation)

    async def all_transactions(self) -> list[dict[str, Any]]:
        def operation() -> list[dict[str, Any]]:
            with self._connect() as db:
                return [
                    dict(item)
                    for item in db.execute(
                        """
                        SELECT id, tx_type, amount, category, object_name, comment,
                               created_at, created_by, source
                        FROM transactions
                        WHERE deleted_at IS NULL
                        ORDER BY created_at DESC, id DESC
                        """
                    ).fetchall()
                ]

        return await self._run(operation)

    async def category_totals(self, start: datetime | None = None, end: datetime | None = None) -> list[dict[str, Any]]:
        clauses = ["deleted_at IS NULL"]
        params: list[Any] = []
        if start:
            clauses.append("created_at >= ?")
            params.append(start.strftime("%Y-%m-%d %H:%M:%S"))
        if end:
            clauses.append("created_at < ?")
            params.append(end.strftime("%Y-%m-%d %H:%M:%S"))
        where = " AND ".join(clauses)

        def operation() -> list[dict[str, Any]]:
            with self._connect() as db:
                return [
                    dict(item)
                    for item in db.execute(
                        f"""
                        SELECT tx_type, category, SUM(amount) AS total, COUNT(*) AS count
                        FROM transactions
                        WHERE {where}
                        GROUP BY tx_type, category
                        ORDER BY tx_type, total DESC
                        """,
                        params,
                    ).fetchall()
                ]

        return await self._run(operation)
