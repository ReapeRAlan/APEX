"""
APEX Database — backwards-compatible entry point.

All imports of `from ..db.database import db` continue to work.
Under the hood, uses SQLAlchemy 2.x via the compat shim.
Set DATABASE_URL for PostgreSQL or leave unset for SQLite fallback.
"""

from .compat import Database, db

__all__ = ["Database", "db"]
