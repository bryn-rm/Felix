"""
Async database helper wrapping asyncpg.

The backend uses the Supabase Postgres connection directly (with the service
role, which bypasses RLS). Every query MUST include user_id in its WHERE
clause — RLS is a safety net for the frontend anon-key path, not a substitute
for correct backend scoping.
"""

import asyncpg
from app.config import settings

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(settings.DATABASE_URL, min_size=2, max_size=10)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


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

    columns = list(data.keys())
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
    columns = list(data.keys())
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
    where_value = data[where_column]
    update_data = {k: v for k, v in data.items() if k != where_column}
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
