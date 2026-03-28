"""
APEX Monitoring Service — periodic background checks on registered areas.

Uses APScheduler (optional dependency) to schedule recurring analysis jobs
that compare deforestation/alert data against configurable thresholds and
dispatch email notifications via AlertService.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

logger = logging.getLogger("apex.monitoring_service")

# ------------------------------------------------------------------
# Graceful APScheduler import
# ------------------------------------------------------------------
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.interval import IntervalTrigger

    _HAS_APSCHEDULER = True
except ImportError:
    _HAS_APSCHEDULER = False
    logger.warning(
        "apscheduler is not installed — monitoring scheduler will not run. "
        "Install it with:  pip install apscheduler"
    )

from .alert_service import AlertService  # noqa: E402


class MonitoringService:
    """
    Manages a set of monitored areas and runs analysis checks
    on a configurable schedule.
    """

    def __init__(self, db):
        self.db = db
        self.alert_service = AlertService()
        self._scheduler = None

    # ------------------------------------------------------------------
    # Scheduler lifecycle
    # ------------------------------------------------------------------

    def start(self):
        """Start the background scheduler and enqueue all active areas."""
        if not _HAS_APSCHEDULER:
            logger.warning("Cannot start scheduler — apscheduler not installed.")
            return

        self._scheduler = BackgroundScheduler(daemon=True)

        for area in self.list_areas():
            if area["active"]:
                self._schedule_area(area)

        self._scheduler.start()
        logger.info("Monitoring scheduler started.")

    def stop(self):
        """Shut down the scheduler gracefully."""
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("Monitoring scheduler stopped.")

    # ------------------------------------------------------------------
    # CRUD helpers
    # ------------------------------------------------------------------

    def add_area(
        self,
        name: str,
        aoi_geojson: str,
        engines: str,
        alert_email: Optional[str],
        threshold_ha: float = 1.0,
        interval_hours: int = 168,
    ) -> int:
        """Register a new area for periodic monitoring. Returns new row id."""
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                """INSERT INTO monitoring_areas
                   (name, aoi_geojson, engines, alert_email,
                    alert_threshold_ha, check_interval_hours, active)
                   VALUES (?, ?, ?, ?, ?, ?, 1)""",
                (name, aoi_geojson, engines, alert_email, threshold_ha, interval_hours),
            )
            conn.commit()
            area_id = cursor.lastrowid

        if self._scheduler and self._scheduler.running:
            area = self._get_area_by_id(area_id)
            if area:
                self._schedule_area(area)

        logger.info("Monitoring area %s (id=%d) registered.", name, area_id)
        return area_id

    def remove_area(self, area_id: int):
        """Deactivate a monitored area and remove its scheduled job."""
        with self.db.get_connection() as conn:
            conn.execute(
                "UPDATE monitoring_areas SET active = 0 WHERE id = ?", (area_id,)
            )
            conn.commit()

        job_id = f"monitor_{area_id}"
        if self._scheduler and self._scheduler.get_job(job_id):
            self._scheduler.remove_job(job_id)

        logger.info("Monitoring area id=%d deactivated.", area_id)

    def delete_area(self, area_id: int):
        """Permanently delete a monitored area and all its alerts."""
        job_id = f"monitor_{area_id}"
        if self._scheduler and self._scheduler.get_job(job_id):
            self._scheduler.remove_job(job_id)

        with self.db.get_connection() as conn:
            conn.execute(
                "DELETE FROM monitoring_alerts WHERE monitoring_area_id = ?",
                (area_id,),
            )
            conn.execute(
                "DELETE FROM monitoring_areas WHERE id = ?", (area_id,)
            )
            conn.commit()
        logger.info("Monitoring area id=%d permanently deleted.", area_id)

    def toggle_area(self, area_id: int) -> bool:
        """Toggle active/inactive state. Returns new active state."""
        area = self._get_area_by_id(area_id)
        if not area:
            raise ValueError(f"Area {area_id} not found")

        new_active = not area["active"]
        with self.db.get_connection() as conn:
            conn.execute(
                "UPDATE monitoring_areas SET active = ? WHERE id = ?",
                (1 if new_active else 0, area_id),
            )
            conn.commit()

        job_id = f"monitor_{area_id}"
        if new_active and self._scheduler:
            self._schedule_area(area)
        elif not new_active and self._scheduler and self._scheduler.get_job(job_id):
            self._scheduler.remove_job(job_id)

        logger.info("Area id=%d toggled to active=%s.", area_id, new_active)
        return new_active

    def list_areas(self) -> List[dict]:
        """Return all monitoring areas (active and inactive)."""
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM monitoring_areas ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_alert_history(self, area_id: int) -> List[dict]:
        """Return alert history for a given monitoring area."""
        with self.db.get_connection() as conn:
            rows = conn.execute(
                """SELECT * FROM monitoring_alerts
                   WHERE monitoring_area_id = ?
                   ORDER BY detected_at DESC""",
                (area_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_alert_count(self, area_id: int) -> int:
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM monitoring_alerts WHERE monitoring_area_id = ?",
                (area_id,),
            ).fetchone()
        return row[0] if row else 0

    def purge_alerts(self, area_id: int) -> int:
        """Delete all alerts for an area. Returns count deleted."""
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM monitoring_alerts WHERE monitoring_area_id = ?",
                (area_id,),
            )
            conn.commit()
            return cursor.rowcount

    # ------------------------------------------------------------------
    # Analyze now — runs the real APEX pipeline
    # ------------------------------------------------------------------

    def analyze_now(self, area_id: int) -> dict:
        """
        Run a real analysis pipeline for the monitored area using its
        configured AOI and engines. Returns summary dict.
        """
        area = self._get_area_by_id(area_id)
        if not area:
            raise ValueError(f"Area {area_id} not found")

        logger.info("Running on-demand analysis for area '%s' (id=%d)", area["name"], area_id)

        try:
            engines = json.loads(area["engines"]) if isinstance(area["engines"], str) else area["engines"]
        except (json.JSONDecodeError, TypeError):
            engines = ["deforestation", "urban_expansion"]

        aoi = json.loads(area["aoi_geojson"]) if isinstance(area["aoi_geojson"], str) else area["aoi_geojson"]

        # Use yesterday to today as date range
        from datetime import date, timedelta
        end_date = date.today().isoformat()
        start_date = (date.today() - timedelta(days=365)).isoformat()

        # Create a real pipeline job
        # NOTE: Do NOT set notify_email here — the monitoring service handles
        # its own email sending with area_name context and threshold checks.
        # Setting notify_email would cause _send_completion_email to fire a
        # duplicate, less-informative email.
        job_id = str(uuid.uuid4())

        from ..db.database import db as _db
        with _db.get_connection() as conn:
            conn.execute(
                """INSERT INTO jobs (id, status, progress, current_step, aoi_geojson, engines, date_range_start, date_range_end)
                   VALUES (?, 'pending', 0, 'Iniciando...', ?, ?, ?, ?)""",
                (job_id, json.dumps(aoi), json.dumps(engines), start_date, end_date),
            )
            conn.commit()

        # Run the pipeline — run_pipeline is async, so we need to handle it properly
        from ..pipeline import run_pipeline
        req_data = {
            "aoi": aoi,
            "date_range": [start_date, end_date],
            "engines": engines,
        }

        new_alerts = []
        total_new_ha = 0.0

        try:
            # run_pipeline is synchronous — call it directly
            run_pipeline(job_id, req_data)

            # Read results from DB to generate alerts
            with _db.get_connection() as conn:
                rows = conn.execute(
                    "SELECT engine, stats_json FROM analysis_results WHERE job_id = ?",
                    (job_id,),
                ).fetchall()

            for row in rows:
                engine = row["engine"]
                try:
                    stats = json.loads(row["stats_json"]) if row["stats_json"] else {}
                except (json.JSONDecodeError, TypeError):
                    stats = {}

                ha = stats.get("area_ha", 0) or stats.get("total_loss_ha", 0) or stats.get("total_burned_ha", 0) or stats.get("total_area_ha", 0) or 0

                if ha > 0:
                    new_alerts.append({
                        "type": engine,
                        "area_ha": round(ha, 2),
                        "detail": f"job={job_id[:8]}, features={stats.get('n_features', stats.get('total_alerts', '?'))}",
                    })
                    total_new_ha += ha

        except Exception as exc:
            logger.error("Pipeline error for monitoring area %d: %s", area_id, exc)
            new_alerts.append({
                "type": "error",
                "area_ha": 0,
                "detail": str(exc)[:200],
            })

        # Persist results
        now_ts = datetime.now(timezone.utc).isoformat()
        with self.db.get_connection() as conn:
            for alert in new_alerts:
                conn.execute(
                    """INSERT INTO monitoring_alerts
                       (monitoring_area_id, alert_type, area_ha, details_json, email_sent)
                       VALUES (?, ?, ?, ?, 0)""",
                    (area_id, alert["type"], alert["area_ha"], json.dumps(alert)),
                )
            conn.execute(
                "UPDATE monitoring_areas SET last_checked = ? WHERE id = ?",
                (now_ts, area_id),
            )
            conn.commit()

        # Email notification — always send report if email is configured
        threshold = area.get("alert_threshold_ha", 1.0) or 1.0
        email = area.get("alert_email")
        email_sent = False
        exceeds_threshold = total_new_ha >= threshold

        if email:
            try:
                # Build full analysis results dict from the pipeline output
                with _db.get_connection() as conn:
                    result_rows = conn.execute(
                        "SELECT * FROM analysis_results WHERE job_id = ?",
                        (job_id,),
                    ).fetchall()

                layers = {}
                for rrow in result_rows:
                    try:
                        layers[rrow["engine"]] = {
                            "geojson": json.loads(rrow["geojson"]) if rrow["geojson"] else {},
                            "stats": json.loads(rrow["stats_json"]) if rrow["stats_json"] else {},
                        }
                    except (json.JSONDecodeError, TypeError):
                        pass

                # Send full branded report with PDF attachment
                success = self.alert_service.send_analysis_report_email(
                    to_email=email,
                    job_id=job_id,
                    results=layers,
                    analysis_type="monitoring",
                    area_name=area["name"],
                    date_range=[start_date, end_date],
                )

                if success:
                    email_sent = True
                    with self.db.get_connection() as conn:
                        conn.execute(
                            """UPDATE monitoring_alerts
                               SET email_sent = 1
                               WHERE monitoring_area_id = ? AND email_sent = 0""",
                            (area_id,),
                        )
                        conn.commit()
                    logger.info(
                        "Report email with PDF sent for area '%s' — %d alerts, %.2f ha (threshold=%s).",
                        area["name"], len(new_alerts), total_new_ha,
                        "EXCEEDED" if exceeds_threshold else "below",
                    )
                else:
                    logger.warning("Failed to send report email for area '%s'", area["name"])

            except Exception as exc:
                logger.error("Email send error for area %d: %s", area_id, exc)

        return {
            "area_id": area_id,
            "job_id": job_id,
            "alerts": len(new_alerts),
            "total_ha": round(total_new_ha, 2),
            "email_sent": email_sent,
            "exceeds_threshold": exceeds_threshold,
        }

    # ------------------------------------------------------------------
    # Legacy _check_area (used by scheduler — delegates to analyze_now)
    # ------------------------------------------------------------------

    def _check_area(self, area_id: int):
        """APScheduler callback — runs analyze_now."""
        area = self._get_area_by_id(area_id)
        if not area or not area["active"]:
            logger.debug("Area %d not active or not found — skipping.", area_id)
            return
        try:
            result = self.analyze_now(area_id)
            logger.info("Scheduled check for area %d complete: %s", area_id, result)
        except Exception as exc:
            logger.error("Scheduled check for area %d failed: %s", area_id, exc)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_area_by_id(self, area_id: int) -> Optional[dict]:
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM monitoring_areas WHERE id = ?", (area_id,)
            ).fetchone()
        return dict(row) if row else None

    def _schedule_area(self, area: dict):
        """Add an APScheduler job for the given area dict."""
        if not self._scheduler:
            return
        job_id = f"monitor_{area['id']}"
        hours = area.get("check_interval_hours", 168) or 168
        self._scheduler.add_job(
            self._check_area,
            trigger=IntervalTrigger(hours=hours),
            args=[area["id"]],
            id=job_id,
            replace_existing=True,
        )
        logger.debug(
            "Scheduled area '%s' (id=%d) every %d hours.",
            area["name"], area["id"], hours,
        )
