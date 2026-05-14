"""
Three-layer memory for Felix.

Layer 1 — User profile memory (user_memory table)
    Slow-changing JSON profile + preferences. Cached in-process with a short
    TTL so Claude calls can inject context without hitting the DB every time.

Layer 2 — Session summaries (session_summaries table)
    Short summaries of finished chat sessions. The most recent 3 are injected
    on a new session so Felix retains continuity.

Layer 3 — Episodic memory (memory_episodes table)
    Distilled events (emails, meetings, commitments, decisions, significant
    chats) with OpenAI embeddings. Retrieved via hybrid scoring that combines
    cosine similarity, recency, entity match, and importance.

Design notes
    • Every retrieval has a hard timeout — if we exceed the budget we proceed
      with whatever context we already have. Memory augments a request; it
      never blocks it.
    • Embedding generation is always background-triggered; episodes created
      without embeddings are backfilled by a scheduled job.
    • RLS is on every table. The backend uses the service role and therefore
      MUST include user_id in every query.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import httpx

from app import db
from app.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Token budgets — see cross-cutting concerns in the spec
# ---------------------------------------------------------------------------

PROFILE_TOKEN_BUDGET  = 500
SESSIONS_TOKEN_BUDGET = 300
EPISODES_TOKEN_BUDGET = 800

# Hybrid scoring weights (tuned conservatively)
W_SEMANTIC   = 0.55
W_RECENCY    = 0.20
W_ENTITY     = 0.15
W_IMPORTANCE = 0.10

EPISODIC_RETRIEVAL_TIMEOUT_S = 0.5     # 500ms
EMBEDDING_TIMEOUT_S          = 3.0
EMBEDDING_MODEL              = "text-embedding-3-small"  # 1536 dims
EMBEDDING_DIMENSIONS         = 1536

PROFILE_CACHE_TTL_S = 120


# Cached pgvector availability. None means "unknown, probe on first use".
_pgvector_available: bool | None = None


# ---------------------------------------------------------------------------
# Tokeniser (very cheap approximation — keep under budget)
# ---------------------------------------------------------------------------

def _approx_tokens(text: str) -> int:
    """Cheap proxy: ~4 characters per token for English."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def _truncate_to_tokens(text: str, budget: int) -> str:
    if _approx_tokens(text) <= budget:
        return text
    char_budget = budget * 4
    cut = text[:char_budget]
    # Try to break on a newline so the cut isn't mid-sentence
    nl = cut.rfind("\n")
    if nl > char_budget // 2:
        cut = cut[:nl]
    return cut.rstrip() + "\n…"


# ---------------------------------------------------------------------------
# Layer 1 · user profile cache
# ---------------------------------------------------------------------------

# user_id → (row_dict, expiry_monotonic)
_profile_cache: dict[str, tuple[dict, float]] = {}


def _cache_expiry() -> float:
    return time.monotonic() + PROFILE_CACHE_TTL_S


def _invalidate_profile(user_id: str) -> None:
    _profile_cache.pop(user_id, None)


async def _load_profile_row(user_id: str) -> dict:
    row = await db.query_one(
        "SELECT profile, preferences, updated_at FROM user_memory WHERE user_id = $1",
        user_id,
    )
    if row is None:
        row = {"profile": {}, "preferences": {}, "updated_at": None}
    else:
        # asyncpg jsonb codec already decodes to dict, but be defensive
        if not isinstance(row.get("profile"), dict):
            row["profile"] = {}
        if not isinstance(row.get("preferences"), dict):
            row["preferences"] = {}
    return row


async def get_user_profile(user_id: str) -> dict:
    """Return cached profile row ({profile, preferences, updated_at})."""
    now = time.monotonic()
    cached = _profile_cache.get(user_id)
    if cached and cached[1] > now:
        return cached[0]
    try:
        row = await _load_profile_row(user_id)
    except Exception:
        logger.exception("Failed to load user memory for %s — returning empty", user_id)
        return {"profile": {}, "preferences": {}, "updated_at": None}
    _profile_cache[user_id] = (row, _cache_expiry())
    return row


def format_profile_context(profile: dict, preferences: dict) -> str:
    """Render the profile + preferences as concise natural language."""
    if not profile and not preferences:
        return ""
    lines: list[str] = []

    # Profile facts — a small, human-readable bullet list
    if profile:
        pieces: list[str] = []
        for key in ("name", "role", "company", "timezone"):
            val = profile.get(key)
            if val:
                pieces.append(f"{key.title()}: {val}")

        contacts = profile.get("key_contacts")
        if isinstance(contacts, list) and contacts:
            rendered = []
            for c in contacts[:8]:
                if isinstance(c, dict):
                    name = c.get("name") or c.get("email") or ""
                    rel = c.get("relationship") or c.get("role") or ""
                    if name and rel:
                        rendered.append(f"{name} ({rel})")
                    elif name:
                        rendered.append(name)
                elif isinstance(c, str):
                    rendered.append(c)
            if rendered:
                pieces.append("Key contacts: " + ", ".join(rendered))

        style = profile.get("communication_style")
        if isinstance(style, str) and style:
            pieces.append(f"Communication style: {style}")

        other = {
            k: v for k, v in profile.items()
            if k not in {"name", "role", "company", "timezone", "key_contacts", "communication_style"}
            and v not in (None, "", [], {})
        }
        for k, v in list(other.items())[:6]:
            if isinstance(v, (dict, list)):
                v = json.dumps(v, default=str)
            pieces.append(f"{k}: {v}")

        if pieces:
            lines.append("User profile:\n- " + "\n- ".join(pieces))

    if preferences:
        pref_pieces = []
        for k, v in preferences.items():
            if v in (None, "", [], {}):
                continue
            if isinstance(v, (dict, list)):
                v = json.dumps(v, default=str)
            pref_pieces.append(f"{k}: {v}")
        if pref_pieces:
            lines.append("User preferences:\n- " + "\n- ".join(pref_pieces))

    return _truncate_to_tokens("\n\n".join(lines), PROFILE_TOKEN_BUDGET)


async def manual_update(
    user_id: str,
    *,
    profile_patch: dict | None = None,
    preferences_patch: dict | None = None,
    clear_profile_keys: Iterable[str] | None = None,
    clear_preference_keys: Iterable[str] | None = None,
) -> dict:
    """
    Patch-merge the profile and preferences for a user.

    Manually set values take precedence over automatic extraction — each
    merged key is recorded with `_source = "manual"` inside the JSON so the
    background extractor knows not to overwrite it.
    """
    current = await _load_profile_row(user_id)
    profile = dict(current.get("profile") or {})
    prefs = dict(current.get("preferences") or {})
    meta_profile = profile.get("_source", {}) if isinstance(profile.get("_source"), dict) else {}
    meta_prefs = prefs.get("_source", {}) if isinstance(prefs.get("_source"), dict) else {}

    if profile_patch:
        for k, v in profile_patch.items():
            if k == "_source":
                continue
            profile[k] = v
            meta_profile[k] = "manual"
    if clear_profile_keys:
        for k in clear_profile_keys:
            profile.pop(k, None)
            meta_profile.pop(k, None)
    if profile_patch or clear_profile_keys:
        profile["_source"] = meta_profile

    if preferences_patch:
        for k, v in preferences_patch.items():
            if k == "_source":
                continue
            prefs[k] = v
            meta_prefs[k] = "manual"
    if clear_preference_keys:
        for k in clear_preference_keys:
            prefs.pop(k, None)
            meta_prefs.pop(k, None)
    if preferences_patch or clear_preference_keys:
        prefs["_source"] = meta_prefs

    row = await db.upsert(
        "user_memory",
        {
            "user_id":     user_id,
            "profile":     profile,
            "preferences": prefs,
            "updated_at":  datetime.now(timezone.utc),
        },
        conflict_columns=["user_id"],
    )
    _invalidate_profile(user_id)
    return row or {"profile": profile, "preferences": prefs}


async def auto_merge_extraction(user_id: str, extracted: dict) -> dict:
    """
    Merge automatically-extracted profile facts into user_memory.

    Rules:
      • Never overwrite a key whose _source is "manual".
      • Detect contradictions (different non-empty value) and resolve in
        favour of the new value, logging the change.
    """
    if not isinstance(extracted, dict):
        return {}

    profile_in: dict = extracted.get("profile") or {}
    prefs_in: dict = extracted.get("preferences") or {}

    current = await _load_profile_row(user_id)
    profile = dict(current.get("profile") or {})
    prefs = dict(current.get("preferences") or {})
    src_profile = profile.get("_source") if isinstance(profile.get("_source"), dict) else {}
    src_prefs = prefs.get("_source") if isinstance(prefs.get("_source"), dict) else {}
    src_profile = dict(src_profile or {})
    src_prefs = dict(src_prefs or {})

    contradictions: list[dict] = []

    def _merge(target: dict, src_meta: dict, new_data: dict) -> None:
        for k, v in new_data.items():
            if k == "_source" or v in (None, "", [], {}):
                continue
            if src_meta.get(k) == "manual":
                continue
            existing = target.get(k)
            if existing and existing != v:
                contradictions.append({"key": k, "old": existing, "new": v})
                logger.info(
                    "memory: contradiction for user=%s key=%s old=%r new=%r",
                    user_id, k, existing, v,
                )
            target[k] = v
            src_meta[k] = "auto"

    _merge(profile, src_profile, profile_in)
    _merge(prefs, src_prefs, prefs_in)

    profile["_source"] = src_profile
    prefs["_source"] = src_prefs

    row = await db.upsert(
        "user_memory",
        {
            "user_id":     user_id,
            "profile":     profile,
            "preferences": prefs,
            "updated_at":  datetime.now(timezone.utc),
        },
        conflict_columns=["user_id"],
    )
    _invalidate_profile(user_id)
    return {
        "profile": (row or {}).get("profile", profile),
        "preferences": (row or {}).get("preferences", prefs),
        "contradictions": contradictions,
    }


# ---------------------------------------------------------------------------
# Layer 2 · session summaries
# ---------------------------------------------------------------------------

async def get_recent_session_context(user_id: str, limit: int = 3) -> str:
    """Render the last N session summaries as natural language."""
    try:
        rows = await db.query(
            """
            SELECT summary, open_items, created_at
            FROM session_summaries
            WHERE user_id = $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            user_id, limit,
        )
    except Exception:
        logger.exception("Failed to load session summaries for user %s", user_id)
        return ""
    if not rows:
        return ""

    lines: list[str] = []
    for r in rows:
        created_at = r.get("created_at")
        when = ""
        if isinstance(created_at, datetime):
            when = created_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        summary = (r.get("summary") or "").strip()
        if not summary:
            continue
        block = f"[{when}] {summary}" if when else summary
        open_items = r.get("open_items") or []
        if isinstance(open_items, list) and open_items:
            rendered = []
            for it in open_items[:3]:
                if isinstance(it, str):
                    rendered.append(it)
                elif isinstance(it, dict):
                    text = it.get("item") or it.get("summary") or it.get("description")
                    if text:
                        rendered.append(text)
            if rendered:
                block += "\n  Open items: " + "; ".join(rendered)
        lines.append(block)

    text = "Recent session context:\n" + "\n\n".join(lines)
    return _truncate_to_tokens(text, SESSIONS_TOKEN_BUDGET)


async def store_session_summary(
    *,
    user_id: str,
    summary: str,
    open_items: list,
    session_metadata: dict | None = None,
) -> dict | None:
    if not summary.strip():
        return None
    return await db.insert(
        "session_summaries",
        {
            "user_id":          user_id,
            "summary":          summary.strip(),
            "open_items":       open_items or [],
            "session_metadata": session_metadata or {},
        },
    )


# ---------------------------------------------------------------------------
# Layer 3 · episodic memory
# ---------------------------------------------------------------------------

_OPENAI_URL = "https://api.openai.com/v1/embeddings"


def _embedding_api_key() -> str | None:
    """Look up the embedding provider API key.

    Kept as a runtime lookup rather than a Pydantic setting so the memory
    system can no-op gracefully in environments that haven't provisioned an
    embedding provider yet.
    """
    return os.getenv("OPENAI_API_KEY") or None


async def _generate_embedding(text: str) -> list[float] | None:
    key = _embedding_api_key()
    if not key or not text.strip():
        return None
    try:
        async with httpx.AsyncClient(timeout=EMBEDDING_TIMEOUT_S) as client:
            resp = await client.post(
                _OPENAI_URL,
                headers={"Authorization": f"Bearer {key}"},
                json={"model": EMBEDDING_MODEL, "input": text[:8000]},
            )
            resp.raise_for_status()
            data = resp.json()
            vec = data["data"][0]["embedding"]
            if isinstance(vec, list):
                return [float(x) for x in vec]
    except Exception:
        logger.warning("Embedding generation failed; episode will be stored without vector.")
    return None


def _vector_literal(vec: list[float]) -> str:
    """Render a Python list as a pgvector literal: '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"


async def pgvector_available() -> bool:
    """Return whether the current DB schema supports pgvector operations."""
    global _pgvector_available
    if _pgvector_available is not None:
        return _pgvector_available

    try:
        row = await db.query_one(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'memory_episodes'
                  AND column_name = 'embedding'
                  AND udt_name = 'vector'
            ) AS has_vector
            """
        )
        _pgvector_available = bool((row or {}).get("has_vector"))
    except Exception:
        logger.warning("pgvector capability probe failed; falling back to non-vector mode.", exc_info=True)
        _pgvector_available = False

    return _pgvector_available


async def create_episode(
    *,
    user_id: str,
    episode_type: str,
    summary: str,
    entities: list | None = None,
    importance: float = 0.5,
    source_type: str | None = None,
    source_id: str | None = None,
    occurred_at: datetime | None = None,
    embedding: list[float] | None = None,
) -> dict | None:
    """Insert an episode row. Embedding is optional (backfilled later)."""
    if not summary.strip():
        return None
    occurred = occurred_at or datetime.now(timezone.utc)
    importance = max(0.0, min(1.0, float(importance)))

    row = await db.insert(
        "memory_episodes",
        {
            "user_id":      user_id,
            "episode_type": episode_type,
            "summary":      summary.strip(),
            "entities":     entities or [],
            "importance":   importance,
            "source_type":  source_type,
            "source_id":    source_id,
            "occurred_at":  occurred,
            # embedding is set via a dedicated UPDATE below — asyncpg doesn't
            # have a Python type for pgvector without an extension.
        },
    )
    if row and embedding is not None:
        try:
            if await pgvector_available():
                await db.execute(
                    "UPDATE memory_episodes SET embedding = $1::vector WHERE id = $2",
                    _vector_literal(embedding), row["id"],
                )
            else:
                await db.execute(
                    "UPDATE memory_episodes SET embedding = $1 WHERE id = $2",
                    json.dumps(embedding), row["id"],
                )
        except Exception:
            logger.warning("Failed to persist embedding for episode %s", row.get("id"))
    return row


async def create_episode_with_embedding(
    *,
    user_id: str,
    episode_type: str,
    summary: str,
    entities: list | None = None,
    importance: float = 0.5,
    source_type: str | None = None,
    source_id: str | None = None,
    occurred_at: datetime | None = None,
) -> dict | None:
    """Convenience — generates embedding then inserts. Safe to call from bg tasks."""
    vec = await _generate_embedding(summary)
    return await create_episode(
        user_id=user_id,
        episode_type=episode_type,
        summary=summary,
        entities=entities,
        importance=importance,
        source_type=source_type,
        source_id=source_id,
        occurred_at=occurred_at,
        embedding=vec,
    )


async def backfill_missing_embeddings(user_id: str | None = None, limit: int = 100) -> int:
    """Generate embeddings for episodes that don't have them yet."""
    vector_supported = await pgvector_available()
    where = "embedding IS NULL"
    args: list = []
    if user_id:
        where += " AND user_id = $1"
        args.append(user_id)
    rows = await db.query(
        f"SELECT id, summary FROM memory_episodes WHERE {where} "
        f"ORDER BY created_at DESC LIMIT {int(limit)}",
        *args,
    )
    filled = 0
    for r in rows:
        vec = await _generate_embedding(r.get("summary") or "")
        if vec is None:
            continue
        try:
            if vector_supported:
                await db.execute(
                    "UPDATE memory_episodes SET embedding = $1::vector WHERE id = $2",
                    _vector_literal(vec), r["id"],
                )
            else:
                await db.execute(
                    "UPDATE memory_episodes SET embedding = $1 WHERE id = $2",
                    json.dumps(vec), r["id"],
                )
            filled += 1
        except Exception:
            logger.warning("Embedding backfill failed for episode %s", r.get("id"))
    return filled


# ── Retrieval ────────────────────────────────────────────────────────────────

_ENTITY_RE = re.compile(r"\b([A-Z][a-zA-Z]{2,}(?:\s+[A-Z][a-zA-Z]{2,}){0,2})\b")


def _extract_query_entities(query: str) -> list[str]:
    """Crude NE extraction — capitalised tokens. Good enough for boosting."""
    return list({m.group(0) for m in _ENTITY_RE.finditer(query or "")})


async def retrieve_episodes(
    *,
    user_id: str,
    query: str,
    top_k: int = 10,
    entity_hints: list[str] | None = None,
) -> list[dict]:
    """
    Hybrid retrieval. Falls back gracefully when embeddings are unavailable
    (in which case scoring is recency + entity + importance only).
    """
    entities = entity_hints or _extract_query_entities(query)
    vector_supported = await pgvector_available()
    vec = await _generate_embedding(query) if vector_supported else None

    # Fetch a wider candidate set so ranking sees something beyond the
    # tightest cosine neighbourhood.
    try:
        if vector_supported and vec is not None:
            rows = await db.query(
                """
                SELECT id, episode_type, summary, entities, importance,
                       source_type, source_id, occurred_at,
                       1 - (embedding <=> $2::vector) AS semantic
                FROM memory_episodes
                WHERE user_id = $1 AND embedding IS NOT NULL
                ORDER BY embedding <=> $2::vector
                LIMIT 50
                """,
                user_id, _vector_literal(vec),
            )
        else:
            rows = await db.query(
                """
                SELECT id, episode_type, summary, entities, importance,
                       source_type, source_id, occurred_at,
                       NULL::float8 AS semantic
                FROM memory_episodes
                WHERE user_id = $1
                ORDER BY occurred_at DESC
                LIMIT 50
                """,
                user_id,
            )
    except Exception:
        logger.exception("Episode retrieval query failed for user %s", user_id)
        return []

    now = datetime.now(timezone.utc)
    scored: list[tuple[float, dict]] = []
    entity_set = {e.lower() for e in entities}

    for r in rows:
        semantic = r.get("semantic")
        if semantic is None:
            # Default neutral similarity when no vector is present
            semantic_s = 0.5
        else:
            semantic_s = max(0.0, min(1.0, float(semantic)))

        occurred = r.get("occurred_at") or now
        if isinstance(occurred, str):
            try:
                occurred = datetime.fromisoformat(occurred)
            except ValueError:
                occurred = now
        age_days = max(0.0, (now - occurred).total_seconds() / 86400.0)
        # Boost within last 7 days, taper quickly after
        recency_s = max(0.0, 1.0 - age_days / 7.0) if age_days <= 7 else max(0.0, 0.5 - age_days / 90.0)

        ep_entities = r.get("entities") or []
        ep_ent_set = set()
        for ent in ep_entities:
            if isinstance(ent, str):
                ep_ent_set.add(ent.lower())
            elif isinstance(ent, dict):
                name = ent.get("name") or ent.get("value") or ""
                if name:
                    ep_ent_set.add(name.lower())
        entity_s = 1.0 if entity_set and entity_set & ep_ent_set else 0.0

        importance_s = float(r.get("importance") or 0.5)

        score = (
            W_SEMANTIC   * semantic_s
            + W_RECENCY    * recency_s
            + W_ENTITY     * entity_s
            + W_IMPORTANCE * importance_s
        )
        scored.append((score, dict(r)))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [row for _, row in scored[:top_k]]


def format_episodes_context(episodes: list[dict]) -> str:
    """Render episodes as a bounded natural-language context block."""
    if not episodes:
        return ""
    lines = ["Relevant past events:"]
    used = _approx_tokens(lines[0])
    for ep in episodes:
        summary = (ep.get("summary") or "").strip()
        if not summary:
            continue
        occurred = ep.get("occurred_at")
        when = ""
        if isinstance(occurred, datetime):
            when = occurred.astimezone(timezone.utc).strftime("%Y-%m-%d")
        kind = ep.get("episode_type", "event")
        entry = f"- [{when} · {kind}] {summary}"
        entry_tokens = _approx_tokens(entry)
        if used + entry_tokens > EPISODES_TOKEN_BUDGET:
            break
        lines.append(entry)
        used += entry_tokens
    return "\n".join(lines)


async def safe_retrieve_context(
    *,
    user_id: str,
    query: str,
    feature: str,
    top_k: int = 10,
) -> str:
    """
    Retrieve + format with a hard timeout so a slow embedding/query doesn't
    bog down the user-facing request. On timeout or error returns "".
    """
    started = time.monotonic()
    try:
        episodes = await asyncio.wait_for(
            retrieve_episodes(user_id=user_id, query=query, top_k=top_k),
            timeout=EPISODIC_RETRIEVAL_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        logger.info("Episodic retrieval timed out for user=%s feature=%s", user_id, feature)
        await _log_memory_op(
            user_id=user_id, operation="retrieve", feature=feature,
            episodes_hit=0, latency_ms=int((time.monotonic() - started) * 1000),
            metadata={"timeout": True},
        )
        return ""
    except Exception:
        logger.exception("Episodic retrieval failed for user=%s feature=%s", user_id, feature)
        return ""

    text = format_episodes_context(episodes)
    await _log_memory_op(
        user_id=user_id, operation="retrieve", feature=feature,
        episodes_hit=len(episodes),
        latency_ms=int((time.monotonic() - started) * 1000),
        tokens_used=_approx_tokens(text),
        metadata={"query_entities": _extract_query_entities(query)},
    )
    return text


# ---------------------------------------------------------------------------
# Unified context builder — called from every AI surface
# ---------------------------------------------------------------------------

async def build_memory_context(
    *,
    user_id: str | None,
    feature: str,
    query: str = "",
    include_sessions: bool = False,
    include_episodes: bool = False,
    session_id: str | None = None,
) -> str:
    """
    Produce the memory prelude for a Claude call.

    `include_sessions` is enabled for chat/voice surfaces.
    `include_episodes` is enabled for chat + email drafting; skip for calendar
    operations or triage where latency is critical.
    """
    if not user_id:
        return ""
    sections: list[str] = []

    # Layer 1 — always
    try:
        row = await get_user_profile(user_id)
        prelude = format_profile_context(row.get("profile") or {}, row.get("preferences") or {})
        if prelude:
            sections.append(prelude)
    except Exception:
        logger.exception("build_memory_context: profile failed for user %s", user_id)

    # Layer 2 — sessions (only where continuity matters)
    if include_sessions:
        try:
            sess = await get_recent_session_context(user_id)
            if sess:
                sections.append(sess)
        except Exception:
            logger.exception("build_memory_context: sessions failed for user %s", user_id)

    # Layer 3 — episodic retrieval (best-effort, timeout-bounded)
    if include_episodes and query:
        try:
            eps = await safe_retrieve_context(
                user_id=user_id, query=query, feature=feature, top_k=10,
            )
            if eps:
                sections.append(eps)
        except Exception:
            logger.exception("build_memory_context: episodes failed for user %s", user_id)

    return "\n\n".join(sections).strip()


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------

async def _log_memory_op(
    *,
    user_id: str,
    operation: str,
    feature: str | None = None,
    episodes_hit: int | None = None,
    latency_ms: int | None = None,
    tokens_used: int | None = None,
    metadata: dict | None = None,
) -> None:
    try:
        await db.insert(
            "memory_operations",
            {
                "user_id":      user_id,
                "operation":    operation,
                "feature":      feature,
                "episodes_hit": episodes_hit,
                "latency_ms":   latency_ms,
                "tokens_used":  tokens_used,
                "metadata":     metadata or {},
            },
        )
    except Exception:
        logger.debug("memory_operations log failed (non-fatal)", exc_info=True)


# ---------------------------------------------------------------------------
# Targeted forgetting
# ---------------------------------------------------------------------------

async def forget_by_topic(user_id: str, topic: str) -> int:
    """
    Delete every episode whose summary or entities reference the given topic.
    Returns the number of rows removed.
    """
    if not topic.strip():
        return 0
    like = f"%{topic.lower()}%"
    status = await db.execute(
        """
        DELETE FROM memory_episodes
        WHERE user_id = $1
          AND (
            LOWER(summary) LIKE $2
            OR EXISTS (
                SELECT 1 FROM jsonb_array_elements_text(
                    CASE
                        WHEN jsonb_typeof(entities) = 'array' THEN entities
                        ELSE '[]'::jsonb
                    END
                ) AS e
                WHERE LOWER(e) LIKE $2
            )
          )
        """,
        user_id, like,
    )
    # asyncpg status is "DELETE <n>"
    try:
        return int(status.rsplit(" ", 1)[-1])
    except Exception:
        return 0


async def prune_low_value_episodes() -> int:
    """Delete episodes older than 60 days with importance < 0.2 (any user)."""
    status = await db.execute(
        """
        DELETE FROM memory_episodes
        WHERE importance < 0.2
          AND occurred_at < NOW() - INTERVAL '60 days'
        """,
    )
    try:
        return int(status.rsplit(" ", 1)[-1])
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Distillation + profile extraction (Claude Haiku)
#
# These are the write-side counterparts to the retrieval helpers above. They
# live here (rather than in ai_service) so the memory layer owns the full
# loop: extract → merge → store, retrieve → format → inject.
# ---------------------------------------------------------------------------

async def _claude_json(*, feature: str, prompt: str, user_id: str | None, max_tokens: int = 600) -> dict | None:
    """Call Haiku, parse JSON, log the call. Returns parsed dict or None."""
    from anthropic import AsyncAnthropic

    from app.services.ai_service import log_ai_call

    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=120.0, max_retries=2)
    started = time.monotonic()
    response = None
    success = True
    parse_error = False
    error_message: str | None = None
    try:
        response = await client.messages.create(
            model=settings.ANTHROPIC_MODEL_FAST,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        raw = re.sub(r"\s*```$", "", raw)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            parse_error = True
            error_message = f"JSONDecodeError: {e}"
            return None
    except Exception as e:
        success = False
        error_message = f"{type(e).__name__}: {e}"
        return None
    finally:
        await log_ai_call(
            feature=feature,
            model=settings.ANTHROPIC_MODEL_FAST,
            response=response,
            started_at=started,
            user_id=user_id,
            success=success,
            parse_error=parse_error,
            error_message=error_message,
        )


async def distil_and_store_episode(
    *,
    user_id: str,
    episode_type: str,
    content: str,
    source_type: str | None = None,
    source_id: str | None = None,
    occurred_at: datetime | None = None,
    min_importance: float = 0.3,
) -> dict | None:
    """
    Distil a piece of raw activity into a summary + entities + importance
    via Claude Haiku, then create the episode (with embedding). Fire-and-
    forget safe: swallows exceptions and returns None.

    Skipped silently when the distiller judges importance below the floor.
    """
    from app.prompts.memory import EPISODE_DISTILLATION_PROMPT

    content = (content or "").strip()
    if not content:
        return None

    try:
        prompt = EPISODE_DISTILLATION_PROMPT.format(
            episode_type=episode_type, content=content[:6000],
        )
        data = await _claude_json(
            feature="episode_distil", prompt=prompt, user_id=user_id, max_tokens=400,
        )
        if not data:
            return None
        summary = (data.get("summary") or "").strip()
        if not summary:
            return None
        importance = float(data.get("importance") or 0.5)
        if importance < min_importance:
            return None
        entities = data.get("entities") or []
        if not isinstance(entities, list):
            entities = []

        row = await create_episode_with_embedding(
            user_id=user_id,
            episode_type=episode_type,
            summary=summary,
            entities=entities,
            importance=importance,
            source_type=source_type,
            source_id=source_id,
            occurred_at=occurred_at,
        )
        if row:
            await _log_memory_op(
                user_id=user_id, operation="create_episode",
                feature=episode_type,
                metadata={"importance": importance, "source_type": source_type},
            )
        return row
    except Exception:
        logger.exception("distil_and_store_episode failed for user %s", user_id)
        return None


async def extract_and_merge_profile(
    *,
    user_id: str,
    activity_snippet: str,
) -> dict:
    """
    Run PROFILE_EXTRACTION_PROMPT over recent activity and merge the result
    into user_memory (respecting manual flags).
    """
    from app.prompts.memory import PROFILE_EXTRACTION_PROMPT

    if not activity_snippet.strip():
        return {"profile": {}, "preferences": {}, "contradictions": []}

    current = await _load_profile_row(user_id)
    existing = json.dumps(
        {"profile": current.get("profile") or {}, "preferences": current.get("preferences") or {}},
        default=str,
    )
    prompt = PROFILE_EXTRACTION_PROMPT.format(
        existing_profile=existing[:4000],
        activity_snippet=activity_snippet[:8000],
    )
    data = await _claude_json(
        feature="profile_extract", prompt=prompt, user_id=user_id, max_tokens=600,
    )
    if not data:
        return {"profile": {}, "preferences": {}, "contradictions": []}
    return await auto_merge_extraction(user_id, data)
