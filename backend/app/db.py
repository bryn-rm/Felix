"""
Async database helper wrapping asyncpg.

The backend uses the Supabase Postgres connection directly (with the service
role, which bypasses RLS). Every query MUST include user_id in its WHERE
clause — RLS is a safety net for the frontend anon-key path, not a substitute
for correct backend scoping.
"""

import asyncio
import json as _json
import logging
import re
from datetime import date, datetime, time
from functools import partial

import asyncpg
from app.config import settings

logger = logging.getLogger(__name__)


def _json_default(obj):
    if isinstance(obj, (datetime, date, time)):
        return obj.isoformat()
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


_json_dumps = partial(_json.dumps, default=_json_default)

_SAFE_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


def _safe_id(name: str) -> str:
    """Validate a SQL identifier (table or column name) against an allowlist pattern."""
    if not _SAFE_IDENTIFIER.match(name):
        raise ValueError(f"Unsafe SQL identifier: {name!r}")
    return name

_pool: asyncpg.Pool | None = None


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Register JSON/JSONB codecs so Python dicts/lists are auto-serialised."""
    await conn.set_type_codec(
        "jsonb",
        encoder=_json_dumps,
        decoder=_json.loads,
        schema="pg_catalog",
    )
    await conn.set_type_codec(
        "json",
        encoder=_json_dumps,
        decoder=_json.loads,
        schema="pg_catalog",
    )


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            settings.DATABASE_URL,
            min_size=2,
            max_size=10,
            init=_init_connection,
            statement_cache_size=0,
        )
    return _pool


async def close_pool() -> None:
    global _pool, _lock_conn
    if _pool:
        await _pool.close()
        _pool = None
    if _lock_conn and not _lock_conn.is_closed():
        await _lock_conn.close()
    _lock_conn = None


# ---------------------------------------------------------------------------
# Advisory locks — dedicated connection, NOT the pool.
#
# Session-scoped advisory locks live on the connection that took them, so a
# holder must keep its connection for the lock's lifetime. Taking them from
# the pool deadlocks the scheduler: interval jobs share one process-start
# anchor, so at the hourly tick ~10 jobs fire together — exactly max_size=10
# — each holding a pooled connection for its whole run while the job body
# waits forever for a second one. One dedicated connection per process holds
# every lock instead; keys differ per job, and pool.acquire() is never
# involved.
#
# Requires a session-mode connection (direct or Supabase pooler port 5432,
# per LAUNCH.md). A transaction-mode pooler (port 6543) would route each
# statement to a different server session and silently break the locks.
# ---------------------------------------------------------------------------

_lock_conn: asyncpg.Connection | None = None
# asyncpg connections don't allow concurrent queries; serialize the brief
# lock/unlock statements (never held during job bodies).
_lock_conn_mutex = asyncio.Lock()


async def _get_lock_conn() -> asyncpg.Connection:
    global _lock_conn
    if _lock_conn is None or _lock_conn.is_closed():
        # Port 6543 is Supabase's transaction-mode pooler: statements hop
        # between server sessions, so session advisory locks would appear to
        # succeed while guarding nothing. Point the lock connection at the
        # session pooler (port 5432) or a direct connection instead.
        if ":6543" in settings.DATABASE_URL:
            logger.critical(
                "DATABASE_URL uses the transaction-mode pooler (port 6543) — "
                "advisory job locks will NOT provide cross-process exclusion. "
                "Use the session pooler (port 5432) for the lock connection."
            )
        _lock_conn = await asyncpg.connect(
            settings.DATABASE_URL, statement_cache_size=0
        )
    return _lock_conn


async def try_advisory_lock(key: str) -> bool:
    """Try to take a session advisory lock; False if another holder has it.

    Locks taken by this process share one session, so this does NOT guard
    against overlap within the process (same-session re-acquire succeeds) —
    APScheduler's max_instances=1 already covers that. It guards against
    other processes: a second replica or an overlapping deploy.
    """
    async with _lock_conn_mutex:
        conn = await _get_lock_conn()
        return await conn.fetchval(
            "SELECT pg_try_advisory_lock(hashtext($1)::bigint)", key
        )


async def advisory_unlock(key: str) -> None:
    async with _lock_conn_mutex:
        if _lock_conn is None or _lock_conn.is_closed():
            # The lock session died; the server already released its locks.
            return
        await _lock_conn.fetchval(
            "SELECT pg_advisory_unlock(hashtext($1)::bigint)", key
        )


async def query(sql: str, *args) -> list[dict]:
    """Run a SELECT and return all rows as dicts."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
        return [dict(r) for r in rows]


async def query_one(sql: str, *args) -> dict | None:
    """Run a SELECT and return the first row as a dict, or None."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, *args)
        return dict(row) if row else None


async def execute(sql: str, *args) -> str:
    """Run an INSERT / UPDATE / DELETE. Returns the status string."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.execute(sql, *args)


async def upsert(table: str, data: dict, conflict_columns: list[str] | None = None) -> dict | None:
    """
    INSERT … ON CONFLICT DO UPDATE for the given table.

    conflict_columns defaults to ['user_id'] for single-owner tables,
    or pass a custom list (e.g. ['id', 'user_id']).
    """
    if conflict_columns is None:
        conflict_columns = ["user_id"]

    _safe_id(table)
    columns = list(data.keys())
    for col in columns:
        _safe_id(col)
    placeholders = [f"${i + 1}" for i in range(len(columns))]
    update_set = ", ".join(
        f"{col} = EXCLUDED.{col}"
        for col in columns
        if col not in conflict_columns
    )
    conflict_target = ", ".join(conflict_columns)

    sql = (
        f"INSERT INTO {table} ({', '.join(columns)}) "
        f"VALUES ({', '.join(placeholders)}) "
        f"ON CONFLICT ({conflict_target}) DO UPDATE SET {update_set} "
        f"RETURNING *"
    )

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, *data.values())
        return dict(row) if row else None


async def insert(table: str, data: dict) -> dict | None:
    """INSERT a row and return it."""
    _safe_id(table)
    columns = list(data.keys())
    for col in columns:
        _safe_id(col)
    placeholders = [f"${i + 1}" for i in range(len(columns))]
    sql = (
        f"INSERT INTO {table} ({', '.join(columns)}) "
        f"VALUES ({', '.join(placeholders)}) "
        f"RETURNING *"
    )
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, *data.values())
        return dict(row) if row else None


async def update(table: str, data: dict, where_column: str = "user_id") -> dict | None:
    """UPDATE a row identified by where_column and return it."""
    _safe_id(table)
    _safe_id(where_column)
    where_value = data[where_column]
    update_data = {k: v for k, v in data.items() if k != where_column}
    for col in update_data:
        _safe_id(col)
    set_clauses = [f"{col} = ${i + 1}" for i, col in enumerate(update_data.keys())]
    sql = (
        f"UPDATE {table} SET {', '.join(set_clauses)} "
        f"WHERE {where_column} = ${len(update_data) + 1} "
        f"RETURNING *"
    )
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, *update_data.values(), where_value)
        return dict(row) if row else None
