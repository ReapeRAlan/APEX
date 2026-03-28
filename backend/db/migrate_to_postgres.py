"""
Migrate APEX data from SQLite → PostgreSQL.

Usage:
    python -m backend.db.migrate_to_postgres

Reads SQLITE_PATH (default: ./db/apex.sqlite) and DATABASE_URL (must be set).
Copies all rows from the 5 legacy tables into the new PostgreSQL schema.
"""

import json
import os
import sqlite3
import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("apex.migrate")


def migrate():
    sqlite_path = os.getenv("SQLITE_PATH", os.path.join("db", "apex.sqlite"))
    database_url = os.getenv("DATABASE_URL", "")

    if not database_url:
        logger.error("DATABASE_URL not set. Export it before running migration.")
        sys.exit(1)

    if not os.path.exists(sqlite_path):
        logger.warning("SQLite file not found at %s — nothing to migrate.", sqlite_path)
        return

    # ── Connect to SQLite source ──
    src = sqlite3.connect(sqlite_path)
    src.row_factory = sqlite3.Row
    logger.info("Connected to SQLite: %s", sqlite_path)

    # ── Connect to PostgreSQL target ──
    from sqlalchemy import create_engine, text
    from .models import Base

    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)

    pg_engine = create_engine(database_url)
    Base.metadata.create_all(bind=pg_engine)
    logger.info("PostgreSQL schema created/verified.")

    # ── Migrate tables ──
    tables = [
        ("jobs", [
            "id", "status", "progress", "current_step", "logs",
            "aoi_geojson", "engines", "date_range_start", "date_range_end",
            "notify_email", "created_at", "completed_at",
        ]),
        ("analysis_results", [
            "id", "job_id", "engine", "geojson", "stats_json", "tile_path",
        ]),
        ("gee_cache", [
            "id", "aoi_hash", "date_range", "tile_path", "downloaded_at",
        ]),
        ("monitoring_areas", [
            "id", "name", "aoi_geojson", "engines", "alert_email",
            "alert_threshold_ha", "check_interval_hours", "last_checked",
            "created_at", "active", "notes",
        ]),
        ("monitoring_alerts", [
            "id", "monitoring_area_id", "detected_at", "alert_type",
            "area_ha", "details_json", "email_sent",
        ]),
    ]

    with pg_engine.begin() as pg_conn:
        for table_name, columns in tables:
            try:
                rows = src.execute(f"SELECT * FROM {table_name}").fetchall()
            except sqlite3.OperationalError:
                logger.warning("Table %s not found in SQLite — skipping.", table_name)
                continue

            if not rows:
                logger.info("Table %s: 0 rows — skipping.", table_name)
                continue

            # Get actual columns from first row
            actual_cols = rows[0].keys()
            # Use intersection with expected columns
            cols = [c for c in columns if c in actual_cols]

            col_list = ", ".join(cols)
            param_list = ", ".join(f":{c}" for c in cols)

            insert_sql = text(f"INSERT INTO {table_name} ({col_list}) VALUES ({param_list}) ON CONFLICT DO NOTHING")

            batch = []
            for row in rows:
                record = {c: row[c] for c in cols}
                batch.append(record)

            pg_conn.execute(insert_sql, batch)
            logger.info("Table %s: migrated %d rows.", table_name, len(batch))

    # ── Fix sequences for auto-increment columns ──
    with pg_engine.begin() as pg_conn:
        for table_name in ["analysis_results", "gee_cache", "monitoring_areas", "monitoring_alerts"]:
            try:
                pg_conn.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('{table_name}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {table_name}), 0) + 1, false)"
                ))
            except Exception as e:
                logger.warning("Could not fix sequence for %s: %s", table_name, e)

    src.close()
    logger.info("Migration complete!")


if __name__ == "__main__":
    migrate()
