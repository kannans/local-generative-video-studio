"""SQLite-backed persistent state for continuous local video studio sessions."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiosqlite


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _decode_json(value: str) -> Any:
    return json.loads(value)


@dataclass(frozen=True, slots=True)
class StudioSession:
    id: str
    title: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    id: int
    session_id: str
    role: str
    content: str
    created_at: str


@dataclass(frozen=True, slots=True)
class GenerationState:
    session_id: str
    active_seed: int | None
    base_latent_ref: str | None
    timeline: list[dict[str, Any]]
    updated_at: str


@dataclass(frozen=True, slots=True)
class GenerationIteration:
    id: int
    session_id: str
    iteration: int
    seed: int | None
    base_latent_ref: str | None
    timeline_changes: list[dict[str, Any]]
    metadata: dict[str, Any]
    created_at: str


class SessionNotFoundError(KeyError):
    """Raised when an operation targets a session that does not exist."""


class SessionStore:
    """CRUD store for conversations, generation continuity, and timeline edits."""

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path)

    async def initialize(self) -> None:
        """Create the database schema if it has not yet been initialized."""
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        async with self._connect() as database:
            await database.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversation_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK(role IN ('system', 'user', 'assistant', 'tool')),
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS generation_state (
                    session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
                    active_seed INTEGER,
                    base_latent_ref TEXT,
                    timeline_json TEXT NOT NULL DEFAULT '[]',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS generation_iterations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    iteration INTEGER NOT NULL,
                    seed INTEGER,
                    base_latent_ref TEXT,
                    timeline_changes_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(session_id, iteration)
                );
                CREATE INDEX IF NOT EXISTS idx_messages_session_created
                    ON conversation_messages(session_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_iterations_session_iteration
                    ON generation_iterations(session_id, iteration);
                """
            )
            await database.commit()

    async def create_session(self, title: str | None = None) -> StudioSession:
        """Create a new studio session with empty generation state."""
        await self.initialize()
        session_id = str(uuid4())
        created_at = _now()
        async with self._connect() as database:
            await database.execute(
                "INSERT INTO sessions (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (session_id, title, created_at, created_at),
            )
            await database.execute(
                "INSERT INTO generation_state (session_id, timeline_json, updated_at) VALUES (?, '[]', ?)",
                (session_id, created_at),
            )
            await database.commit()
        return StudioSession(session_id, title, created_at, created_at)

    async def get_session(self, session_id: str) -> StudioSession:
        """Load one session or raise SessionNotFoundError."""
        async with self._connect() as database:
            cursor = await database.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
            row = await cursor.fetchone()
        if row is None:
            raise SessionNotFoundError(session_id)
        return StudioSession(row["id"], row["title"], row["created_at"], row["updated_at"])

    async def list_sessions(self, limit: int = 100, offset: int = 0) -> list[StudioSession]:
        """Return recent sessions, newest first."""
        if limit < 1 or offset < 0:
            raise ValueError("limit must be positive and offset must not be negative")
        async with self._connect() as database:
            cursor = await database.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ? OFFSET ?", (limit, offset)
            )
            rows = await cursor.fetchall()
        return [StudioSession(row["id"], row["title"], row["created_at"], row["updated_at"]) for row in rows]

    async def delete_session(self, session_id: str) -> None:
        """Delete a session and all dependent conversation and generation records."""
        async with self._connect() as database:
            cursor = await database.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            await database.commit()
        if cursor.rowcount != 1:
            raise SessionNotFoundError(session_id)

    async def append_message(self, session_id: str, role: str, content: str) -> ConversationMessage:
        """Append a validated conversation message and update the session timestamp."""
        if role not in {"system", "user", "assistant", "tool"}:
            raise ValueError("role must be system, user, assistant, or tool")
        if not content.strip():
            raise ValueError("content must not be empty")
        created_at = _now()
        async with self._connect() as database:
            await self._ensure_session(database, session_id)
            cursor = await database.execute(
                "INSERT INTO conversation_messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (session_id, role, content, created_at),
            )
            await self._touch_session(database, session_id, created_at)
            await database.commit()
        return ConversationMessage(cursor.lastrowid, session_id, role, content, created_at)

    async def list_messages(self, session_id: str) -> list[ConversationMessage]:
        """Return a session's conversation in chronological order."""
        async with self._connect() as database:
            await self._ensure_session(database, session_id)
            cursor = await database.execute(
                "SELECT * FROM conversation_messages WHERE session_id = ? ORDER BY id", (session_id,)
            )
            rows = await cursor.fetchall()
        return [ConversationMessage(row["id"], row["session_id"], row["role"], row["content"], row["created_at"]) for row in rows]

    async def update_generation_state(
        self,
        session_id: str,
        active_seed: int | None = None,
        base_latent_ref: str | None = None,
        timeline: list[dict[str, Any]] | None = None,
    ) -> GenerationState:
        """Replace supplied continuity state fields while retaining omitted values."""
        current = await self.get_generation_state(session_id)
        updated_at = _now()
        next_state = GenerationState(
            session_id,
            active_seed if active_seed is not None else current.active_seed,
            base_latent_ref if base_latent_ref is not None else current.base_latent_ref,
            timeline if timeline is not None else current.timeline,
            updated_at,
        )
        async with self._connect() as database:
            await database.execute(
                "UPDATE generation_state SET active_seed = ?, base_latent_ref = ?, timeline_json = ?, updated_at = ? WHERE session_id = ?",
                (next_state.active_seed, next_state.base_latent_ref, json.dumps(next_state.timeline), updated_at, session_id),
            )
            await self._touch_session(database, session_id, updated_at)
            await database.commit()
        return next_state

    async def get_generation_state(self, session_id: str) -> GenerationState:
        """Load the current seed, latent reference, and complete timeline for a session."""
        async with self._connect() as database:
            cursor = await database.execute("SELECT * FROM generation_state WHERE session_id = ?", (session_id,))
            row = await cursor.fetchone()
        if row is None:
            raise SessionNotFoundError(session_id)
        return GenerationState(row["session_id"], row["active_seed"], row["base_latent_ref"], _decode_json(row["timeline_json"]), row["updated_at"])

    async def append_iteration(
        self,
        session_id: str,
        timeline_changes: list[dict[str, Any]],
        metadata: dict[str, Any],
        seed: int | None = None,
        base_latent_ref: str | None = None,
    ) -> GenerationIteration:
        """Append an immutable generation iteration and apply its continuity updates."""
        current = await self.get_generation_state(session_id)
        iteration = await self._next_iteration(session_id)
        updated_at = _now()
        resolved_seed = seed if seed is not None else current.active_seed
        resolved_latent = base_latent_ref if base_latent_ref is not None else current.base_latent_ref
        next_timeline = [*current.timeline, *timeline_changes]
        async with self._connect() as database:
            cursor = await database.execute(
                "INSERT INTO generation_iterations (session_id, iteration, seed, base_latent_ref, timeline_changes_json, metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_id, iteration, resolved_seed, resolved_latent, json.dumps(timeline_changes), json.dumps(metadata), updated_at),
            )
            await database.execute(
                "UPDATE generation_state SET active_seed = ?, base_latent_ref = ?, timeline_json = ?, updated_at = ? WHERE session_id = ?",
                (resolved_seed, resolved_latent, json.dumps(next_timeline), updated_at, session_id),
            )
            await self._touch_session(database, session_id, updated_at)
            await database.commit()
        return GenerationIteration(cursor.lastrowid, session_id, iteration, resolved_seed, resolved_latent, timeline_changes, metadata, updated_at)

    async def list_iterations(self, session_id: str) -> list[GenerationIteration]:
        """Return all generation iterations in chronological order."""
        async with self._connect() as database:
            await self._ensure_session(database, session_id)
            cursor = await database.execute(
                "SELECT * FROM generation_iterations WHERE session_id = ? ORDER BY iteration", (session_id,)
            )
            rows = await cursor.fetchall()
        return [
            GenerationIteration(row["id"], row["session_id"], row["iteration"], row["seed"], row["base_latent_ref"], _decode_json(row["timeline_changes_json"]), _decode_json(row["metadata_json"]), row["created_at"])
            for row in rows
        ]

    async def _next_iteration(self, session_id: str) -> int:
        async with self._connect() as database:
            cursor = await database.execute(
                "SELECT COALESCE(MAX(iteration), 0) + 1 AS next_iteration FROM generation_iterations WHERE session_id = ?",
                (session_id,),
            )
            row = await cursor.fetchone()
        return int(row["next_iteration"])

    async def _ensure_session(self, database: aiosqlite.Connection, session_id: str) -> None:
        cursor = await database.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,))
        if await cursor.fetchone() is None:
            raise SessionNotFoundError(session_id)

    @staticmethod
    async def _touch_session(database: aiosqlite.Connection, session_id: str, updated_at: str) -> None:
        await database.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (updated_at, session_id))

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[aiosqlite.Connection]:
        database = await aiosqlite.connect(self.database_path)
        database.row_factory = aiosqlite.Row
        await database.execute("PRAGMA foreign_keys = ON")
        try:
            yield database
        finally:
            await database.close()
