"""
APEX Database Session — SQLAlchemy 2.x async/sync engine.

Supports both PostgreSQL (production) and SQLite (fallback/dev).
Reads DATABASE_URL from environment; defaults to SQLite for backwards compat.
"""

import os
import logging
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

from .models import Base

logger = logging.getLogger("apex.db")

# ── Determine database URL ──
DATABASE_URL = os.getenv("DATABASE_URL", "")

if not DATABASE_URL:
    # Fallback to SQLite for backwards compatibility
    # Resolve relative to the APEX project root (parent of backend/)
    _project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    _default_db = os.path.join(_project_root, "db", "apex.sqlite")
    _db_path = os.getenv("DB_PATH", _default_db)
    DATABASE_URL = f"sqlite:///{_db_path}"
    logger.info("No DATABASE_URL set — falling back to SQLite: %s", _db_path)

# Fix common postgres:// vs postgresql:// issue
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# ── Engine configuration ──
_engine_kwargs = {}

if DATABASE_URL.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    _engine_kwargs["pool_size"] = 10
    _engine_kwargs["max_overflow"] = 20
    _engine_kwargs["pool_pre_ping"] = True

engine = create_engine(DATABASE_URL, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db():
    """Create all tables if they don't exist, then apply migrations."""
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created/verified.")

    # ── Idempotent column migrations for existing databases ──
    if DATABASE_URL.startswith("sqlite"):
        _migrate_sqlite()


def _migrate_sqlite():
    """Add columns that may be missing from older SQLite schemas."""
    migrations = [
        ("analysis_results", "created_at", "DATETIME DEFAULT NULL"),
        ("analysis_results", "validated", "BOOLEAN DEFAULT NULL"),
        ("analysis_results", "updated_at", "DATETIME DEFAULT NULL"),
        ("analysis_results", "aoi_hash", "TEXT DEFAULT NULL"),
        ("analysis_results", "date_start", "TEXT DEFAULT NULL"),
        ("analysis_results", "date_end", "TEXT DEFAULT NULL"),
        ("jobs", "updated_at", "DATETIME DEFAULT NULL"),
        ("jobs", "logs", "TEXT"),
        ("jobs", "notify_email", "TEXT DEFAULT NULL"),
    ]
    with engine.connect() as conn:
        for table, column, col_type in migrations:
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                conn.commit()
                logger.info("Migration: added %s.%s", table, column)
            except Exception:
                # Column already exists — ignore
                conn.rollback()


@contextmanager
def get_db() -> Session:
    """Context manager that yields a DB session and auto-closes."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# Alias used by newer modules (kpi, mlflow_pipeline, etc.)
get_session = get_db


def check_connection() -> bool:
    """Test that the database is reachable."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.error("Database connection failed: %s", e)
        return False
