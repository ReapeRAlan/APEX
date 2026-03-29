"""
Local FIRMS data loader for APEX.

Indexes downloaded NASA FIRMS JSON files into a SQLite database
for sub-second queries instead of 21s+ per API call.

Expected directory: APEX/LocalData/ with subdirectories containing
fire_archive_*.json and fire_nrt_*.json files.
"""

import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Path to local FIRMS data
_THIS_DIR = Path(__file__).resolve().parent
_LOCAL_DATA_DIR = _THIS_DIR.parent.parent / "LocalData"
_LOCAL_DB_PATH = _LOCAL_DATA_DIR / "firms_index.sqlite"

# Source mapping: filename code → logical source name
_FILE_SOURCE_MAP = {
    "SV-C2": "VIIRS_SNPP",
    "M-C61": "MODIS",
    "J1V-C2": "VIIRS_NOAA20",
}

# Columns we need from the JSON records
_KEEP_COLS = [
    "latitude", "longitude", "acq_date", "acq_time",
    "confidence", "brightness", "bright_ti4", "bright_ti5",
    "bright_t31", "frp", "scan", "track", "satellite",
    "instrument", "daynight", "type", "version",
]


def local_data_available() -> bool:
    """Check if local FIRMS data directory exists with JSON files."""
    if not _LOCAL_DATA_DIR.is_dir():
        return False
    return any(_LOCAL_DATA_DIR.rglob("fire_archive_*.json"))


def local_db_ready() -> bool:
    """Check if the indexed SQLite DB exists and has data."""
    if not _LOCAL_DB_PATH.is_file():
        return False
    try:
        conn = sqlite3.connect(str(_LOCAL_DB_PATH))
        cur = conn.execute("SELECT COUNT(*) FROM hotspots")
        count = cur.fetchone()[0]
        conn.close()
        return count > 0
    except Exception:
        return False


def build_local_db(force: bool = False) -> str:
    """
    Build SQLite index from downloaded FIRMS JSON files.

    Returns status message. Idempotent — skips if DB already exists
    unless force=True.
    """
    if not local_data_available():
        return f"No local data found in {_LOCAL_DATA_DIR}"

    if local_db_ready() and not force:
        conn = sqlite3.connect(str(_LOCAL_DB_PATH))
        count = conn.execute("SELECT COUNT(*) FROM hotspots").fetchone()[0]
        conn.close()
        return f"DB already indexed: {count:,} records at {_LOCAL_DB_PATH}"

    logger.info("[FIRMS-Local] Building SQLite index from %s ...", _LOCAL_DATA_DIR)
    t0 = time.monotonic()

    # Remove old DB if forcing rebuild
    if _LOCAL_DB_PATH.exists():
        _LOCAL_DB_PATH.unlink()

    conn = sqlite3.connect(str(_LOCAL_DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")  # speed up bulk insert
    conn.execute("PRAGMA cache_size=-200000")  # 200MB cache

    conn.execute("""
        CREATE TABLE hotspots (
            latitude    REAL NOT NULL,
            longitude   REAL NOT NULL,
            acq_date    TEXT NOT NULL,
            acq_time    TEXT,
            confidence  TEXT,
            brightness  REAL,
            bright_ti4  REAL,
            bright_ti5  REAL,
            bright_t31  REAL,
            frp         REAL,
            scan        REAL,
            track       REAL,
            satellite   TEXT,
            instrument  TEXT,
            daynight    TEXT,
            type        TEXT,
            version     TEXT,
            source      TEXT NOT NULL
        )
    """)

    total_inserted = 0

    for subdir in sorted(_LOCAL_DATA_DIR.iterdir()):
        if not subdir.is_dir():
            continue
        for json_file in sorted(subdir.glob("fire_*.json")):
            # Determine source from filename
            source_code = None
            for code, name in _FILE_SOURCE_MAP.items():
                if code in json_file.name:
                    source_code = code
                    source_name = name
                    break
            if not source_code:
                logger.warning("[FIRMS-Local] Unknown source in %s, skipping", json_file.name)
                continue

            # Determine if archive or NRT
            if "archive" in json_file.name:
                src_suffix = "_SP"
            else:
                src_suffix = "_NRT"
            full_source = source_name + src_suffix

            logger.info("[FIRMS-Local] Loading %s (%s) ...", json_file.name, full_source)
            file_t0 = time.monotonic()

            with open(json_file, "r", encoding="utf-8") as f:
                records = json.load(f)

            # Insert in batches
            batch = []
            batch_size = 50000
            for rec in records:
                row = (
                    _safe_float(rec.get("latitude")),
                    _safe_float(rec.get("longitude")),
                    rec.get("acq_date", ""),
                    rec.get("acq_time", ""),
                    str(rec.get("confidence", "")),
                    _safe_float(rec.get("brightness")),
                    _safe_float(rec.get("bright_ti4")),
                    _safe_float(rec.get("bright_ti5")),
                    _safe_float(rec.get("bright_t31")),
                    _safe_float(rec.get("frp")),
                    _safe_float(rec.get("scan")),
                    _safe_float(rec.get("track")),
                    rec.get("satellite", ""),
                    rec.get("instrument", ""),
                    rec.get("daynight", ""),
                    str(rec.get("type", "")),
                    rec.get("version", ""),
                    full_source,
                )
                batch.append(row)
                if len(batch) >= batch_size:
                    conn.executemany(
                        "INSERT INTO hotspots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        batch,
                    )
                    total_inserted += len(batch)
                    batch.clear()

            if batch:
                conn.executemany(
                    "INSERT INTO hotspots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    batch,
                )
                total_inserted += len(batch)

            conn.commit()
            elapsed = time.monotonic() - file_t0
            logger.info(
                "[FIRMS-Local] %s: %d records in %.1fs",
                json_file.name, len(records), elapsed,
            )

    # Create indexes for fast querying
    logger.info("[FIRMS-Local] Creating indexes...")
    conn.execute("CREATE INDEX idx_date ON hotspots (acq_date)")
    conn.execute("CREATE INDEX idx_bbox ON hotspots (latitude, longitude)")
    conn.execute("CREATE INDEX idx_date_bbox ON hotspots (acq_date, latitude, longitude)")
    conn.commit()

    # Analyze for query optimizer
    conn.execute("ANALYZE")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.close()

    elapsed = time.monotonic() - t0
    msg = f"Indexed {total_inserted:,} records in {elapsed:.0f}s → {_LOCAL_DB_PATH}"
    logger.info("[FIRMS-Local] %s", msg)
    return msg


def query_local(
    bbox: tuple[float, float, float, float],
    date_start: str,
    date_end: str,
) -> list[dict]:
    """
    Query local FIRMS SQLite DB for detections within bbox and date range.

    Args:
        bbox: (west, south, east, north) in EPSG:4326
        date_start: YYYY-MM-DD
        date_end: YYYY-MM-DD

    Returns:
        List of detection dicts (same format as API response).
    """
    if not local_db_ready():
        return []

    west, south, east, north = bbox
    t0 = time.monotonic()

    conn = sqlite3.connect(str(_LOCAL_DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        """
        SELECT * FROM hotspots
        WHERE acq_date >= ? AND acq_date <= ?
          AND latitude >= ? AND latitude <= ?
          AND longitude >= ? AND longitude <= ?
        """,
        (date_start, date_end, south, north, west, east),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    elapsed = time.monotonic() - t0
    logger.info(
        "[FIRMS-Local] Query %s→%s bbox=(%.2f,%.2f,%.2f,%.2f): %d rows in %.3fs",
        date_start, date_end, west, south, east, north, len(rows), elapsed,
    )

    # Add _source_key for compatibility with API path
    for r in rows:
        r["_source_key"] = r.pop("source", "LOCAL")

    return rows


def get_date_range() -> Optional[tuple[str, str]]:
    """Return (min_date, max_date) from local DB, or None if not available."""
    if not local_db_ready():
        return None
    try:
        conn = sqlite3.connect(str(_LOCAL_DB_PATH))
        cur = conn.execute("SELECT MIN(acq_date), MAX(acq_date) FROM hotspots")
        row = cur.fetchone()
        conn.close()
        if row and row[0]:
            return (row[0], row[1])
    except Exception:
        pass
    return None


def _safe_float(val) -> Optional[float]:
    """Convert to float, return None if not possible."""
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
