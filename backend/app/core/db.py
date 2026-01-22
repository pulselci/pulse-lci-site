from __future__ import annotations

import threading
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.core.config import settings

_lock = threading.Lock()
_pool: ConnectionPool | None = None

def _make_pool() -> ConnectionPool:
    # Important: open=False prevents background worker threads from trying immediately.
    # We'll open it only when we need it, and re-open if needed.
    return ConnectionPool(
        conninfo=settings.DATABASE_URL,
        min_size=1,
        max_size=5,
        kwargs={"row_factory": dict_row},
        open=False,
        timeout=10,  # shorter so requests fail fast if DB is unreachable
    )

def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        with _lock:
            if _pool is None:
                _pool = _make_pool()
    # Ensure it's open (safe to call multiple times)
    _pool.open()
    return _pool

def get_conn():
    # Acquire a connection when needed
    pool = get_pool()
    return pool.connection()

def close_pool():
    global _pool
    with _lock:
        if _pool is not None:
            _pool.close()
            _pool = None
