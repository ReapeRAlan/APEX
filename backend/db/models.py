"""
SQLAlchemy 2.x ORM models for APEX — PostgreSQL/PostGIS backend.

Replaces the raw sqlite3 schema from database.py.
All geospatial columns use GeoAlchemy2 for PostGIS integration.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True)
    status = Column(String, nullable=False, default="queued")
    progress = Column(Integer, default=0)
    current_step = Column(String, nullable=True)
    logs = Column(Text, default="[]")
    aoi_geojson = Column(Text, nullable=False)
    engines = Column(Text, nullable=False)
    date_range_start = Column(String, nullable=True)
    date_range_end = Column(String, nullable=True)
    notify_email = Column(String, nullable=True)
    created_at = Column(DateTime, default=func.now())
    completed_at = Column(DateTime, nullable=True)

    results = relationship("AnalysisResult", back_populates="job", cascade="all, delete-orphan")


class AnalysisResult(Base):
    __tablename__ = "analysis_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String, ForeignKey("jobs.id"), nullable=False)
    engine = Column(String, nullable=False)
    geojson = Column(Text, nullable=True)
    stats_json = Column(Text, nullable=True)
    tile_path = Column(String, nullable=True)

    job = relationship("Job", back_populates="results")


class GEECache(Base):
    __tablename__ = "gee_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    aoi_hash = Column(String, nullable=False)
    date_range = Column(String, nullable=False)
    tile_path = Column(String, nullable=False)
    downloaded_at = Column(DateTime, default=func.now())

    __table_args__ = (
        Index("idx_gee_cache", "aoi_hash", "date_range"),
    )


class MonitoringArea(Base):
    __tablename__ = "monitoring_areas"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    aoi_geojson = Column(Text, nullable=False)
    engines = Column(Text, nullable=False)
    alert_email = Column(String, nullable=True)
    alert_threshold_ha = Column(Float, default=1.0)
    check_interval_hours = Column(Integer, default=168)
    last_checked = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=func.now())
    active = Column(Boolean, default=True)
    notes = Column(Text, nullable=True)

    alerts = relationship("MonitoringAlert", back_populates="area", cascade="all, delete-orphan")


class MonitoringAlert(Base):
    __tablename__ = "monitoring_alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    monitoring_area_id = Column(Integer, ForeignKey("monitoring_areas.id"), nullable=False)
    detected_at = Column(DateTime, default=func.now())
    alert_type = Column(String, nullable=True)
    area_ha = Column(Float, nullable=True)
    details_json = Column(Text, nullable=True)
    email_sent = Column(Boolean, default=False)

    area = relationship("MonitoringArea", back_populates="alerts")


# ── Phase 0.4: H3 Grid ──

class GridCell(Base):
    """National H3 grid cell with territorial metadata."""
    __tablename__ = "grid_cells"

    id = Column(Integer, primary_key=True, autoincrement=True)
    h3_index = Column(String(15), unique=True, nullable=False, index=True)
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)
    estado = Column(String, nullable=True)
    municipio = Column(String, nullable=True)
    tipo_ecosistema = Column(String, nullable=True)
    en_anp = Column(Boolean, default=False)
    nombre_anp = Column(String, nullable=True)
    cuenca_id = Column(String, nullable=True)


# ── Phase 1: Bayesian Fusion ──

class BeliefState(Base):
    """Per-cell Bayesian belief state."""
    __tablename__ = "belief_states"

    id = Column(Integer, primary_key=True, autoincrement=True)
    h3_index = Column(String(15), ForeignKey("grid_cells.h3_index"), nullable=False, index=True)
    timestamp = Column(DateTime, default=func.now(), nullable=False)
    p_sin_ilicito = Column(Float, default=0.85)
    p_tala = Column(Float, default=0.05)
    p_cus_inmobiliario = Column(Float, default=0.05)
    p_frontera_agricola = Column(Float, default=0.05)
    confidence_index = Column(Float, default=0.5)
    last_clean_image = Column(DateTime, nullable=True)
    acquire_commercial_image = Column(Boolean, default=False)
    source_motors = Column(Text, nullable=True)  # JSON: which motors contributed


# ── Phase 3.5: Auth ──

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, nullable=True)
    role = Column(String, nullable=False, default="inspector")
    zona_geografica_ids = Column(Text, nullable=True)  # JSON array of region IDs
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.now())
