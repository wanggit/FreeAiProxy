from __future__ import annotations

from pathlib import Path

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    conv_id      TEXT PRIMARY KEY,
    chat_id      TEXT,
    status       TEXT NOT NULL DEFAULT 'active',
    provider     TEXT NOT NULL DEFAULT 'deepseek',
    mode         TEXT NOT NULL DEFAULT 'chat',
    created_at   REAL NOT NULL,
    last_used_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS history_msgs (
    conv_id TEXT NOT NULL,
    seq     INTEGER NOT NULL,
    role    TEXT NOT NULL,
    content TEXT NOT NULL,
    PRIMARY KEY (conv_id, seq),
    FOREIGN KEY (conv_id) REFERENCES conversations(conv_id)
);

CREATE INDEX IF NOT EXISTS idx_history_conv ON history_msgs (conv_id, seq);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.executescript(_SCHEMA)
        await self._migrate()
        await self._conn.commit()

    async def _migrate(self) -> None:
        async with self.conn.execute("PRAGMA table_info(conversations)") as cur:
            columns = {row[1] for row in await cur.fetchall()}
        if "provider" not in columns:
            await self.conn.execute(
                "ALTER TABLE conversations ADD COLUMN provider TEXT NOT NULL DEFAULT 'deepseek'"
            )
        if "mode" not in columns:
            await self.conn.execute(
                "ALTER TABLE conversations ADD COLUMN mode TEXT NOT NULL DEFAULT 'chat'"
            )

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("database not connected")
        return self._conn

    async def create_conversation(
        self, conv_id: str, provider: str = "deepseek", mode: str = "chat"
    ) -> None:
        import time

        now = time.time()
        await self.conn.execute(
            "INSERT INTO conversations "
            "(conv_id, status, provider, mode, created_at, last_used_at) "
            "VALUES (?, 'active', ?, ?, ?, ?)",
            (conv_id, provider, mode, now, now),
        )
        await self.conn.commit()

    async def get_conversation(self, conv_id: str) -> dict | None:
        async with self.conn.execute(
            "SELECT conv_id, chat_id, status, provider, mode, created_at, last_used_at "
            "FROM conversations WHERE conv_id = ?",
            (conv_id,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def list_active_conversations(
        self, provider: str | None = None, mode: str | None = None
    ) -> list[dict]:
        sql = (
            "SELECT conv_id, chat_id, status, provider, mode, created_at, last_used_at "
            "FROM conversations WHERE status = 'active'"
        )
        params: list[str] = []
        if provider is not None:
            sql += " AND provider = ?"
            params.append(provider)
        if mode is not None:
            sql += " AND mode = ?"
            params.append(mode)
        sql += " ORDER BY last_used_at DESC"
        async with self.conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def get_history(self, conv_id: str) -> list[dict]:
        async with self.conn.execute(
            "SELECT seq, role, content FROM history_msgs WHERE conv_id = ? ORDER BY seq",
            (conv_id,),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def append_history(self, conv_id: str, role: str, content: str) -> None:
        async with self.conn.execute(
            "SELECT COALESCE(MAX(seq), -1) FROM history_msgs WHERE conv_id = ?", (conv_id,)
        ) as cur:
            row = await cur.fetchone()
            next_seq = (row[0] if row else -1) + 1
        await self.conn.execute(
            "INSERT INTO history_msgs (conv_id, seq, role, content) VALUES (?, ?, ?, ?)",
            (conv_id, next_seq, role, content),
        )
        await self.conn.commit()

    async def bind_chat_id(self, conv_id: str, chat_id: str) -> None:
        await self.conn.execute(
            "UPDATE conversations SET chat_id = ?, status = 'active' WHERE conv_id = ?",
            (chat_id, conv_id),
        )
        await self.conn.commit()

    async def mark_dead(self, conv_id: str) -> None:
        await self.conn.execute(
            "UPDATE conversations SET status = 'dead' WHERE conv_id = ?", (conv_id,)
        )
        await self.conn.commit()

    async def touch(self, conv_id: str) -> None:
        import time

        await self.conn.execute(
            "UPDATE conversations SET last_used_at = ? WHERE conv_id = ?",
            (time.time(), conv_id),
        )
        await self.conn.commit()

    async def set_mode(self, conv_id: str, mode: str) -> None:
        await self.conn.execute(
            "UPDATE conversations SET mode = ? WHERE conv_id = ?", (mode, conv_id)
        )
        await self.conn.commit()
