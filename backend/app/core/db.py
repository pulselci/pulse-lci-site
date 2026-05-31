# app/core/db.py

from __future__ import annotations

from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from app.core.config import settings


def open_pool() -> None:
    # Kept for compatibility with app.main
    return None


def close_pool() -> None:
    # Kept for compatibility with app.main
    return None


@contextmanager
def get_conn():
    conn = psycopg.connect(
        settings.DATABASE_URL,
        autocommit=False,
        row_factory=dict_row,
    )
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass