# database.py — async SQLite (dev) / PostgreSQL (prod)
import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite
import asyncpg

from config import DATABASE_URL

logger = logging.getLogger(__name__)

IS_PG = DATABASE_URL.startswith("postgres")

_pool: Optional[asyncpg.Pool] = None
_sqlite_path: Optional[str] = None

SCHEMA_USERS = """
CREATE TABLE IF NOT EXISTS users (
    user_id         BIGINT PRIMARY KEY,
    username        TEXT,
    first_name      TEXT,
    registered_at   TIMESTAMPTZ DEFAULT NOW(),
    last_activity   TIMESTAMPTZ DEFAULT NOW(),
    translations    INTEGER DEFAULT 0,
    banned          BOOLEAN DEFAULT FALSE
);
"""

SCHEMA_TRANSLATIONS = """
CREATE TABLE IF NOT EXISTS translation_log (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL,
    source_lang     TEXT,
    char_count      INTEGER,
    success         BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
"""

SCHEMA_BROADCASTS = """
CREATE TABLE IF NOT EXISTS broadcasts (
    id              BIGSERIAL PRIMARY KEY,
    admin_id        BIGINT NOT NULL,
    total           INTEGER,
    success         INTEGER,
    failed          INTEGER,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
"""

SCHEMA_CONFIG = """
CREATE TABLE IF NOT EXISTS bot_config (
    key             TEXT PRIMARY KEY,
    value           TEXT
);
"""

SCHEMAS_PG = [SCHEMA_USERS, SCHEMA_TRANSLATIONS, SCHEMA_BROADCASTS, SCHEMA_CONFIG]

# SQLite equivalents (SERIAL → INTEGER PRIMARY KEY AUTOINCREMENT, TIMESTAMPTZ → TEXT)
def _sqlite_schema(pg_schema: str) -> str:
    s = pg_schema
    s = s.replace("BIGSERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")
    s = s.replace("TIMESTAMPTZ DEFAULT NOW()", "TEXT DEFAULT CURRENT_TIMESTAMP")
    return s


async def init_db() -> None:
    global _pool, _sqlite_path
    if IS_PG:
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
        async with _pool.acquire() as conn:
            for s in SCHEMAS_PG:
                await conn.execute(s)
        logger.info("PostgreSQL pool ready")
    else:
        # sqlite:///bot.db  →  bot.db
        m = re.match(r"sqlite:///(.+)", DATABASE_URL)
        _sqlite_path = m.group(1) if m else "bot.db"
        async with aiosqlite.connect(_sqlite_path) as db:
            for s in SCHEMAS_PG:
                await db.execute(_sqlite_schema(s))
            await db.commit()
        logger.info("SQLite ready at %s", _sqlite_path)


async def close_db() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


async def _fetch(sql: str, *args) -> List[Any]:
    if IS_PG:
        async with _pool.acquire() as conn:
            return await conn.fetch(sql.replace("?", "$1"), *args) if "?" in sql else await conn.fetch(sql, *args)
    async with aiosqlite.connect(_sqlite_path) as db:
        async with db.execute(sql, args) as cur:
            return await cur.fetchall()


async def _execute(sql: str, *args) -> None:
    if IS_PG:
        async with _pool.acquire() as conn:
            await conn.execute(sql.replace("?", "$1"), *args)
    else:
        async with aiosqlite.connect(_sqlite_path) as db:
            await db.execute(sql, args)
            await db.commit()


async def _fetchrow(sql: str, *args):
    if IS_PG:
        async with _pool.acquire() as conn:
            return await conn.fetchrow(sql.replace("?", "$1"), *args)
    async with aiosqlite.connect(_sqlite_path) as db:
        async with db.execute(sql, args) as cur:
            return await cur.fetchone()


# ---- User operations ----

async def upsert_user(user_id: int, username: Optional[str], first_name: Optional[str]) -> None:
    now = datetime.now(timezone.utc)
    if IS_PG:
        async with _pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO users (user_id, username, first_name, registered_at, last_activity)
                VALUES ($1, $2, $3, $4, $4)
                ON CONFLICT (user_id) DO UPDATE SET
                    username = EXCLUDED.username,
                    first_name = EXCLUDED.first_name,
                    last_activity = EXCLUDED.last_activity
            """, user_id, username, first_name, now)
    else:
        async with aiosqlite.connect(_sqlite_path) as db:
            await db.execute("""
                INSERT INTO users (user_id, username, first_name, registered_at, last_activity)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name,
                    last_activity = excluded.last_activity
            """, (user_id, username, first_name, now.isoformat(), now.isoformat()))
            await db.commit()


async def get_user(user_id: int) -> Optional[Dict]:
    row = await _fetchrow("SELECT * FROM users WHERE user_id = ?", user_id)
    if not row:
        return None
    if IS_PG:
        return dict(row)
    keys = ["user_id", "username", "first_name", "registered_at", "last_activity", "translations", "banned"]
    return dict(zip(keys, row))


async def set_banned(user_id: int, banned: bool) -> bool:
    await _execute("UPDATE users SET banned = ? WHERE user_id = ?", banned, user_id)
    return True


async def increment_translations(user_id: int) -> None:
    await _execute("UPDATE users SET translations = translations + 1 WHERE user_id = ?", user_id)


async def log_translation(user_id: int, source_lang: str, char_count: int, success: bool) -> None:
    await _execute(
        "INSERT INTO translation_log (user_id, source_lang, char_count, success) VALUES (?, ?, ?, ?)",
        user_id, source_lang, char_count, success,
    )


# ---- Stats ----

async def get_stats() -> Dict[str, Any]:
    total_users = await _fetchrow("SELECT COUNT(*) FROM users")
    banned = await _fetchrow("SELECT COUNT(*) FROM users WHERE banned = TRUE")
    total_tr = await _fetchrow("SELECT COUNT(*) FROM translation_log WHERE success = TRUE")
    failed_tr = await _fetchrow("SELECT COUNT(*) FROM translation_log WHERE success = FALSE")

    # Today's counts differ by dialect
    if IS_PG:
        today_tr = await _fetchrow(
            "SELECT COUNT(*) FROM translation_log WHERE success = TRUE AND created_at::date = CURRENT_DATE"
        )
        today_users = await _fetchrow(
            "SELECT COUNT(*) FROM users WHERE registered_at::date = CURRENT_DATE"
        )
        active = await _fetchrow(
            "SELECT COUNT(*) FROM users WHERE last_activity::date = CURRENT_DATE AND banned = FALSE"
        )
    else:
        today_tr = await _fetchrow(
            "SELECT COUNT(*) FROM translation_log WHERE success = TRUE AND date(created_at) = date('now')"
        )
        today_users = await _fetchrow(
            "SELECT COUNT(*) FROM users WHERE date(registered_at) = date('now')"
        )
        active = await _fetchrow(
            "SELECT COUNT(*) FROM users WHERE date(last_activity) = date('now') AND banned = 0"
        )

    broadcasts = await _fetchrow("SELECT COUNT(*) FROM broadcasts")

    def _v(r):
        if r is None:
            return 0
        return r[0] if isinstance(r, (tuple, list)) else list(r.values())[0]

    return {
        "total_users": _v(total_users),
        "active_users": _v(active),
        "banned_users": _v(banned),
        "total_translations": _v(total_tr),
        "today_translations": _v(today_tr),
        "today_new_users": _v(today_users),
        "total_broadcasts": _v(broadcasts),
        "failed_translations": _v(failed_tr),
    }


# ---- Broadcast ----

async def get_all_active_user_ids() -> List[int]:
    rows = await _fetch("SELECT user_id FROM users WHERE banned = FALSE")
    out = []
    for r in rows:
        out.append(r[0] if isinstance(r, (tuple, list)) else r["user_id"])
    return out


async def record_broadcast(admin_id: int, total: int, success: int, failed: int) -> None:
    await _execute(
        "INSERT INTO broadcasts (admin_id, total, success, failed) VALUES (?, ?, ?, ?)",
        admin_id, total, success, failed,
    )
