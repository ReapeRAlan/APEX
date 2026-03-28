"""
APEX Database — Compatibility shim.

Provides the same `db.get_connection()` interface used by existing code
but backed by SQLAlchemy. This allows a gradual migration: old code keeps
working while new code uses the ORM session directly.
"""

import json
import logging
from contextlib import contextmanager

from .session import engine, init_db, SessionLocal
from .models import Base

logger = logging.getLogger("apex.db")


class _RowProxy:
    """Mimics sqlite3.Row so dict-style row["col"] access works."""

    def __init__(self, mapping):
        self._data = dict(mapping)

    def __getitem__(self, key):
        return self._data[key]

    def get(self, key, default=None):
        return self._data.get(key, default)

    def keys(self):
        return self._data.keys()


class _ConnectionProxy:
    """
    Wraps a SQLAlchemy connection to expose the sqlite3-compatible
    .execute() / .fetchone() / .fetchall() / .commit() interface.
    """

    def __init__(self, raw_conn):
        self._conn = raw_conn
        self._last_cursor = None

    def execute(self, sql, params=None):
        from sqlalchemy import text as sa_text

        # Convert ? placeholders to :p0, :p1, ... for SQLAlchemy
        if params and "?" in sql:
            parts = sql.split("?")
            new_sql = parts[0]
            param_dict = {}
            for i, val in enumerate(params):
                key = f"_p{i}"
                new_sql += f":{key}" + parts[i + 1]
                param_dict[key] = val
            result = self._conn.execute(sa_text(new_sql), param_dict)
        elif params:
            result = self._conn.execute(sa_text(sql), params)
        else:
            result = self._conn.execute(sa_text(sql))

        self._last_cursor = result
        return _CursorProxy(result)

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class _CursorProxy:
    """Wraps SQLAlchemy CursorResult to mimic sqlite3 cursor."""

    def __init__(self, result):
        self._result = result
        # For INSERT: try to get lastrowid
        try:
            self.lastrowid = result.lastrowid
        except Exception:
            self.lastrowid = None

    def fetchone(self):
        row = self._result.fetchone()
        if row is None:
            return None
        return _RowProxy(row._mapping)

    def fetchall(self):
        rows = self._result.fetchall()
        return [_RowProxy(r._mapping) for r in rows]


class Database:
    """
    Drop-in replacement for the old sqlite3-based Database class.
    Uses SQLAlchemy under the hood but preserves the same interface.
    """

    def __init__(self):
        init_db()

    def get_connection(self):
        """Return a connection-like object compatible with old code."""
        raw_conn = engine.connect()
        return _ConnectionProxy(raw_conn)


# Singleton — same pattern as old code
db = Database()
