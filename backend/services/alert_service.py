"""
APEX Alert Service — email notifications for analysis and monitoring.

Sends PROFEPA-branded HTML report emails via SMTP with optional PDF
attachments. Supports both manual analysis reports and monitoring alerts.
"""

import os
import asyncio
import smtplib
import logging
import json
import tempfile
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from typing import List, Optional
from datetime import datetime, timedelta

logger = logging.getLogger("apex.alert_service")


# ── Engine display names ──
ENGINE_LABELS = {
    "deforestation": "Deforestacion (Dynamic World)",
    "vegetation": "Clasificacion de vegetacion",
    "urban_expansion": "Expansion urbana",
    "structures": "Deteccion de estructuras",
    "hansen": "Hansen Global Forest Change",
    "alerts": "Alertas GLAD/RADD",
    "drivers": "Drivers de deforestacion (WRI)",
    "fire": "Incendios (MODIS)",
    "sar": "SAR Sentinel-1 (Radar)",
    "crossval": "Validacion cruzada (MapBiomas)",
    "legal_context": "Contexto legal (ANPs)",
}

ENGINE_ICONS = {
    "deforestation": "#f85149",
    "vegetation": "#2ea043",
    "urban_expansion": "#f0883e",
    "structures": "#58a6ff",
    "hansen": "#facc15",
    "alerts": "#dc2626",
    "drivers": "#8b5cf6",
    "fire": "#f97316",
    "sar": "#06b6d4",
    "crossval": "#10b981",
    "legal_context": "#22c55e",
}


class AlertService:
    """Sends styled PROFEPA-branded HTML alert/report emails via SMTP."""

    def __init__(self):
        from ..config import settings
        self.smtp_host = settings.SMTP_HOST
        self.smtp_port = settings.SMTP_PORT
        self.smtp_user = settings.SMTP_USER
        self.smtp_pass = settings.SMTP_PASS
        self.from_email = settings.ALERT_FROM_EMAIL or self.smtp_user

    # ------------------------------------------------------------------
    # Geo helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_geo_info(aoi_geojson: dict) -> dict:
        """Extract centroid, area, bbox, and map links from an AOI GeoJSON."""
        try:
            from shapely.geometry import shape
            geom = shape(aoi_geojson)
            c = geom.centroid
            bounds = geom.bounds  # (minx, miny, maxx, maxy)

            # Approximate area in hectares (at this latitude)
            import math
            mid_lat = (bounds[1] + bounds[3]) / 2
            deg_to_km = 111.32
            area_km2 = geom.area * deg_to_km * deg_to_km * math.cos(math.radians(mid_lat))
            area_ha = area_km2 * 100

            coords_preview = " | ".join(
                [f"{p[0]:.5f},{p[1]:.5f}" for p in list(geom.exterior.coords)[:4]]
            )

            return {
                "centroid_lat": round(c.y, 6),
                "centroid_lon": round(c.x, 6),
                "area_ha": round(area_ha, 2),
                "area_km2": round(area_km2, 2),
                "google_maps": f"https://maps.google.com/?q={c.y},{c.x}&t=k&z=14",
                "google_earth": f"https://earth.google.com/web/@{c.y},{c.x},500a,1000d,35y,0h,0t,0r",
                "bbox": f"{bounds[1]:.5f},{bounds[0]:.5f},{bounds[3]:.5f},{bounds[2]:.5f}",
                "coords_preview": coords_preview,
            }
        except Exception as exc:
            logger.warning("Could not compute geo info: %s", exc)
            return {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def send_alert_email(
        self,
        to_email: str,
        subject: str,
        body_html: str,
        attachment_path: Optional[Path] = None,
    ) -> bool:
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(
                None, self._send_sync, to_email, subject, body_html, attachment_path,
            )
            logger.info("Alert email sent successfully to %s", to_email)
            return True
        except Exception as exc:
            logger.error("Failed to send alert email to %s: %s", to_email, exc)
            return False

    def send_analysis_report_email(
        self,
        to_email: str,
        job_id: str,
        results: dict,
        aoi_info: Optional[dict] = None,
        analysis_type: str = "manual",
        area_name: Optional[str] = None,
        date_range: Optional[List[str]] = None,
    ) -> bool:
        if not self.smtp_user or not self.smtp_pass:
            logger.error("SMTP not configured — cannot send report email")
            return False

        try:
            # Fetch AOI from DB for geo info
            aoi_geojson = None
            try:
                from ..db.database import db
                with db.get_connection() as conn:
                    row = conn.execute(
                        "SELECT aoi_geojson FROM jobs WHERE id = ?", (job_id,)
                    ).fetchone()
                    if row and row["aoi_geojson"]:
                        aoi_geojson = json.loads(row["aoi_geojson"])
            except Exception as exc:
                logger.warning("Could not fetch AOI for job %s: %s", job_id[:8], exc)

            geo = self._get_geo_info(aoi_geojson) if aoi_geojson else {}

            html = self._format_analysis_report_html(
                job_id, results, aoi_info, analysis_type, area_name, date_range, geo
            )

            pdf_path = self._generate_report_pdf(
                job_id, results, analysis_type, area_name, date_range, geo,
                aoi_geojson=aoi_geojson,
            )

            folio = f"PROFEPA-APEX-{job_id[:8].upper()}"
            type_label = {
                "manual": "Analisis Manual",
                "monitoring": "Monitoreo Automatico",
                "timeline": "Analisis Multi-temporal",
            }.get(analysis_type, "Analisis")

            subject = f"[APEX] Reporte {type_label} — {folio}"
            if area_name:
                subject = f"[APEX] Reporte {type_label} — {area_name} ({folio})"

            self._send_sync(to_email, subject, html, pdf_path)

            if pdf_path and Path(pdf_path).exists():
                try:
                    os.unlink(pdf_path)
                except Exception:
                    pass

            logger.info("Analysis report email sent to %s (job=%s)", to_email, job_id[:8])
            return True

        except Exception as exc:
            logger.error("Failed to send analysis report to %s: %s", to_email, exc)
            return False

    def format_alert_html(
        self, area_name: str, alerts: list, total_ha: float
    ) -> str:
        """Build a PROFEPA-branded HTML summary for monitoring alerts."""
        alert_rows = ""
        for idx, alert in enumerate(alerts, start=1):
            a_type = alert.get("type", "Desconocido")
            a_ha = alert.get("area_ha", 0.0)
            a_detail = alert.get("detail", "")
            color = ENGINE_ICONS.get(a_type, "#8b949e")
            alert_rows += (
                f"<tr>"
                f"<td style='padding:10px 8px;border-bottom:1px solid #e5e7eb;font-size:13px;'>{idx}</td>"
                f"<td style='padding:10px 8px;border-bottom:1px solid #e5e7eb;font-size:13px;'>"
                f"<span style='display:inline-block;width:8px;height:8px;border-radius:50%;background:{color};margin-right:6px;'></span>"
                f"{ENGINE_LABELS.get(a_type, a_type)}</td>"
                f"<td style='padding:10px 8px;border-bottom:1px solid #e5e7eb;font-size:13px;font-weight:600;'>{a_ha:.2f} ha</td>"
                f"<td style='padding:10px 8px;border-bottom:1px solid #e5e7eb;font-size:12px;color:#6b7280;'>{a_detail}</td>"
                f"</tr>"
            )

        now_str = datetime.now().strftime("%d/%m/%Y %H:%M")

        html = f"""\
<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0;padding:0;font-family:'Segoe UI',Arial,Helvetica,sans-serif;background:#f0f2f5;">
<table width="100%" cellpadding="0" cellspacing="0">
<tr><td align="center" style="padding:20px 0;">
<table width="640" cellpadding="0" cellspacing="0"
       style="background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 4px 16px rgba(0,0,0,0.08);">
  <tr><td style="background:linear-gradient(135deg,#0b2545 0%,#13315c 100%);padding:28px 32px;">
    <table width="100%"><tr>
      <td><h1 style="margin:0;color:#ffffff;font-size:24px;font-weight:700;">PROFEPA — Sistema APEX</h1>
        <p style="margin:6px 0 0;color:#8da9c4;font-size:13px;">Alerta de Monitoreo Ambiental</p></td>
      <td align="right" style="vertical-align:top;">
        <div style="background:rgba(255,255,255,0.12);border-radius:8px;padding:8px 14px;">
          <span style="color:#8da9c4;font-size:10px;display:block;">FECHA</span>
          <span style="color:#fff;font-size:12px;font-weight:600;">{now_str}</span>
        </div></td>
    </tr></table></td></tr>
  <tr><td style="padding:0;">
    <div style="background:linear-gradient(90deg,#f8714822,#f8714808);border-left:4px solid #f87148;padding:16px 32px;">
      <span style="font-size:28px;vertical-align:middle;">&#9888;</span>
      <span style="font-size:15px;font-weight:600;color:#b45309;margin-left:8px;vertical-align:middle;">
        Se detectaron {len(alerts)} cambio(s) en el area monitoreada</span>
    </div></td></tr>
  <tr><td style="padding:24px 32px;">
    <h2 style="margin:0 0 16px;color:#0b2545;font-size:20px;font-weight:700;">{area_name}</h2>
    <table width="100%"><tr>
      <td width="50%" style="padding-right:8px;">
        <div style="background:#f8f9fa;border-radius:10px;padding:16px;text-align:center;">
          <div style="font-size:28px;font-weight:700;color:#0b2545;">{len(alerts)}</div>
          <div style="font-size:11px;color:#6b7280;text-transform:uppercase;">Alertas</div>
        </div></td>
      <td width="50%" style="padding-left:8px;">
        <div style="background:#fef2f2;border-radius:10px;padding:16px;text-align:center;">
          <div style="font-size:28px;font-weight:700;color:#dc2626;">{total_ha:.2f}</div>
          <div style="font-size:11px;color:#6b7280;text-transform:uppercase;">Hectareas afectadas</div>
        </div></td>
    </tr></table>
    <div style="margin-top:20px;">
      <table width="100%" style="border-collapse:collapse;">
        <thead><tr style="background:#f8f9fa;">
          <th style="padding:10px 8px;text-align:left;font-size:11px;text-transform:uppercase;color:#6b7280;border-bottom:2px solid #e5e7eb;">#</th>
          <th style="padding:10px 8px;text-align:left;font-size:11px;text-transform:uppercase;color:#6b7280;border-bottom:2px solid #e5e7eb;">Tipo</th>
          <th style="padding:10px 8px;text-align:left;font-size:11px;text-transform:uppercase;color:#6b7280;border-bottom:2px solid #e5e7eb;">Superficie</th>
          <th style="padding:10px 8px;text-align:left;font-size:11px;text-transform:uppercase;color:#6b7280;border-bottom:2px solid #e5e7eb;">Detalle</th>
        </tr></thead>
        <tbody>{alert_rows}</tbody>
      </table>
    </div></td></tr>
  <tr><td style="background:linear-gradient(135deg,#0b2545 0%,#13315c 100%);padding:20px 32px;text-align:center;">
    <p style="margin:0 0 4px;color:#8da9c4;font-size:11px;">Este correo fue generado automaticamente por el sistema APEX.</p>
    <p style="margin:0;color:#5a7fa0;font-size:10px;">Subprocuraduria de Recursos Naturales / CEPVR — PROFEPA</p>
  </td></tr>
</table></td></tr></table>
</body></html>"""
        return html

    # ------------------------------------------------------------------
    # Analysis Report HTML
    # ------------------------------------------------------------------

    def _format_analysis_report_html(
        self,
        job_id: str,
        results: dict,
        aoi_info: Optional[dict] = None,
        analysis_type: str = "manual",
        area_name: Optional[str] = None,
        date_range: Optional[List[str]] = None,
        geo: Optional[dict] = None,
    ) -> str:
        """Build a comprehensive PROFEPA-branded HTML report email."""
        folio = f"PROFEPA-APEX-{job_id[:8].upper()}"
        now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
        geo = geo or {}

        type_label = {
            "manual": "Analisis de Deteccion",
            "monitoring": "Monitoreo Automatico",
            "timeline": "Analisis Multi-temporal",
        }.get(analysis_type, "Analisis")

        type_color = {
            "manual": "#2ea043",
            "monitoring": "#f0883e",
            "timeline": "#58a6ff",
        }.get(analysis_type, "#2ea043")

        display_name = area_name or "Area de Interes"

        # Date range display
        date_range_str = ""
        t2_start = t2_end = t1_start = t1_end = ""
        if date_range and len(date_range) >= 2:
            t2_start, t2_end = date_range[0], date_range[1]
            date_range_str = f"{t2_start} — {t2_end}"
            # T1 = one year before T2 start
            try:
                dt = datetime.strptime(t2_start, "%Y-%m-%d")
                t1_start = (dt - timedelta(days=365)).strftime("%Y-%m-%d")
                t1_end = t2_start
            except Exception:
                t1_start = t1_end = ""

        # Build engine results sections
        engine_sections = ""
        total_alerts = 0
        total_ha = 0.0
        engines_analyzed = []

        layers = results if isinstance(results, dict) else {}

        for engine_name, data in layers.items():
            if engine_name in ("timeline_summary",):
                continue

            stats = data.get("stats", data) if isinstance(data, dict) else {}
            geojson_data = data.get("geojson", {}) if isinstance(data, dict) else {}

            label = ENGINE_LABELS.get(engine_name, engine_name)
            color = ENGINE_ICONS.get(engine_name, "#8b949e")
            engines_analyzed.append(label)

            ha = (stats.get("area_ha", 0) or stats.get("total_loss_ha", 0)
                  or stats.get("total_burned_ha", 0) or stats.get("total_area_ha", 0)
                  or stats.get("total_change_ha", 0) or 0)
            features = (stats.get("n_features", 0) or stats.get("total_alerts", 0)
                       or stats.get("fire_count", 0) or stats.get("sar_change_count", 0)
                       or stats.get("count", 0) or len(geojson_data.get("features", [])))

            total_ha += ha
            total_alerts += features

            stat_items = ""
            if ha > 0:
                stat_items += f"""
                <div style="display:inline-block;background:#f8f9fa;border-radius:6px;padding:8px 14px;margin:3px;">
                    <span style="font-size:16px;font-weight:700;color:#0b2545;">{ha:.2f}</span>
                    <span style="font-size:10px;color:#6b7280;display:block;">Hectareas</span>
                </div>"""
            if features > 0:
                stat_items += f"""
                <div style="display:inline-block;background:#f8f9fa;border-radius:6px;padding:8px 14px;margin:3px;">
                    <span style="font-size:16px;font-weight:700;color:#0b2545;">{features}</span>
                    <span style="font-size:10px;color:#6b7280;display:block;">Detecciones</span>
                </div>"""

            extra_info = self._engine_extra_info(engine_name, stats)

            engine_sections += f"""
            <div style="border-left:4px solid {color};padding:12px 16px;margin-bottom:12px;background:#fafafa;border-radius:0 8px 8px 0;">
                <div style="font-size:14px;font-weight:600;color:#0b2545;margin-bottom:6px;">
                    <span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:{color};margin-right:8px;"></span>
                    {label}
                </div>
                <div>{stat_items}</div>
                {extra_info}
            </div>"""

        # Risk level
        risk_level, risk_color, risk_bg = self._compute_risk(total_ha)

        # ── Satellite sources table ──
        sat_rows = ""
        sat_sources = [
            ("Sentinel-2 (ESA)", t1_start, t1_end, t2_start, t2_end, "10m optico"),
            ("Dynamic World (Google)", t1_start, t1_end, t2_start, t2_end, "10m DL"),
            ("Hansen GFC v1.12 (UMD)", "2000", "2024", "Serie historica", "", "30m Landsat"),
            ("GLAD-S2 / RADD (WUR)", "Alertas en tiempo real", "", "Actualizacion semanal", "", "10m SAR+optico"),
            ("MODIS MCD64A1 (NASA)", "Areas quemadas mensuales", "", "Ultimos 12 meses", "", "500m termal"),
            ("Sentinel-1 SAR (ESA)", t1_start, t1_end, t2_start, t2_end, "10m radar"),
        ]
        for i, (sensor, p1s, p1e, p2s, p2e, res) in enumerate(sat_sources):
            bg = "#f8f9fa" if i % 2 == 0 else "#ffffff"
            p1 = f"{p1s} &rarr; {p1e}" if p1e else p1s
            p2 = f"{p2s} &rarr; {p2e}" if p2e else p2s
            sat_rows += f"""<tr style="background:{bg};">
                <td style="padding:6px 8px;font-size:11px;border-bottom:1px solid #e5e7eb;">{sensor}</td>
                <td style="padding:6px 8px;font-size:11px;border-bottom:1px solid #e5e7eb;">{p1}</td>
                <td style="padding:6px 8px;font-size:11px;border-bottom:1px solid #e5e7eb;">{p2}</td>
                <td style="padding:6px 8px;font-size:11px;border-bottom:1px solid #e5e7eb;text-align:center;">{res}</td>
            </tr>"""

        sat_table = f"""
        <h3 style="margin:0 0 10px;color:#0b2545;font-size:15px;font-weight:600;border-bottom:2px solid #e5e7eb;padding-bottom:8px;">
            Imagenes Satelitales Utilizadas
        </h3>
        <table width="100%" style="border-collapse:collapse;">
            <tr style="background:#0b2545;">
                <th style="padding:8px;color:white;font-size:10px;text-transform:uppercase;text-align:left;">Sensor</th>
                <th style="padding:8px;color:white;font-size:10px;text-transform:uppercase;text-align:left;">Periodo T1 (referencia)</th>
                <th style="padding:8px;color:white;font-size:10px;text-transform:uppercase;text-align:left;">Periodo T2 (analisis)</th>
                <th style="padding:8px;color:white;font-size:10px;text-transform:uppercase;text-align:center;">Resolucion</th>
            </tr>
            {sat_rows}
        </table>"""

        # ── Geo location section ──
        geo_section = ""
        if geo:
            clat = geo.get("centroid_lat", "?")
            clon = geo.get("centroid_lon", "?")
            gm_link = geo.get("google_maps", "#")
            ge_link = geo.get("google_earth", "#")
            area_km2 = geo.get("area_km2", 0)
            bbox = geo.get("bbox", "?")

            geo_section = f"""
            <h3 style="margin:0 0 10px;color:#0b2545;font-size:15px;font-weight:600;border-bottom:2px solid #e5e7eb;padding-bottom:8px;">
                Localizacion del Area Analizada
            </h3>
            <table width="100%" cellpadding="0" cellspacing="0"><tr>
                <td width="55%" style="vertical-align:top;">
                    <div style="font-size:12px;color:#374151;line-height:1.8;">
                        <strong>Coordenadas centroide:</strong><br>
                        &nbsp;&nbsp;Lat: {clat}&deg; N &nbsp;|&nbsp; Lon: {clon}&deg; W<br>
                        <strong>Superficie analizada:</strong> {area_km2:.2f} km&sup2;<br>
                        <strong>Bounding box:</strong> {bbox}<br>
                        <strong>Datum:</strong> WGS84 / EPSG:4326
                    </div>
                </td>
                <td width="45%" style="text-align:center;vertical-align:middle;">
                    <a href="{gm_link}" style="display:inline-block;background:#4285f4;color:white;padding:10px 18px;border-radius:6px;text-decoration:none;font-size:12px;font-weight:600;margin:4px;">
                        &#128205; Ver en Google Maps
                    </a><br>
                    <a href="{ge_link}" style="display:inline-block;background:#34a853;color:white;padding:10px 18px;border-radius:6px;text-decoration:none;font-size:12px;font-weight:600;margin:4px;">
                        &#127758; Ver en Google Earth
                    </a>
                </td>
            </tr></table>"""

        # ── Institutional follow-up section ──
        followup_section = f"""
        <h3 style="margin:0 0 10px;color:#0b2545;font-size:15px;font-weight:600;border-bottom:2px solid #e5e7eb;padding-bottom:8px;">
            Seguimiento Institucional Requerido
        </h3>
        <div style="border-left:3px solid #0b2545;padding-left:16px;">
            <div style="margin-bottom:12px;">
                <span style="font-weight:bold;color:#dc2626;">&#128203; INMEDIATO (0-48h)</span><br>
                <span style="font-size:12px;color:#374151;line-height:1.6;">
                    &bull; Registrar incidencia en SIGG-PROFEPA con folio {folio}<br>
                    &bull; Asignar inspector responsable<br>
                    &bull; Verificar si existe CUSTF vigente para el predio
                </span>
            </div>
            <div style="margin-bottom:12px;">
                <span style="font-weight:bold;color:#f97316;">&#128270; CORTO PLAZO (1-7 dias)</span><br>
                <span style="font-size:12px;color:#374151;line-height:1.6;">
                    &bull; Realizar visita de inspeccion y verificacion<br>
                    &bull; Solicitar documentacion al propietario<br>
                    &bull; Tomar evidencia fotografica con coordenadas
                </span>
            </div>
            <div style="margin-bottom:12px;">
                <span style="font-weight:bold;color:#2ea043;">&#128193; EXPEDIENTE</span><br>
                <span style="font-size:12px;color:#374151;line-height:1.6;">
                    &bull; Adjuntar este reporte al expediente PROFEPA<br>
                    &bull; Referencia de imagenes: Sentinel-2 periodo {date_range_str or 'N/A'}<br>
                    &bull; Sistema de referencia: WGS84 / EPSG:4326
                </span>
            </div>
        </div>
        <!-- Chain of custody table -->
        <table width="100%" style="margin-top:12px;border-collapse:collapse;font-size:11px;">
            <tr style="background:#f3f4f6;">
                <td style="padding:6px;border:1px solid #e5e7eb;font-weight:bold;">Folio APEX</td>
                <td style="padding:6px;border:1px solid #e5e7eb;">{folio}</td>
                <td style="padding:6px;border:1px solid #e5e7eb;font-weight:bold;">Generado</td>
                <td style="padding:6px;border:1px solid #e5e7eb;">{now_str}</td>
            </tr>
            <tr>
                <td style="padding:6px;border:1px solid #e5e7eb;font-weight:bold;">Job ID</td>
                <td style="padding:6px;border:1px solid #e5e7eb;">{job_id}</td>
                <td style="padding:6px;border:1px solid #e5e7eb;font-weight:bold;">Tipo analisis</td>
                <td style="padding:6px;border:1px solid #e5e7eb;">{type_label}</td>
            </tr>
            <tr style="background:#f3f4f6;">
                <td style="padding:6px;border:1px solid #e5e7eb;font-weight:bold;">Nivel riesgo</td>
                <td style="padding:6px;border:1px solid #e5e7eb;color:{risk_color};font-weight:bold;">{risk_level}</td>
                <td style="padding:6px;border:1px solid #e5e7eb;font-weight:bold;">Area total</td>
                <td style="padding:6px;border:1px solid #e5e7eb;">{total_ha:.2f} ha afectadas</td>
            </tr>
        </table>"""

        # ── Assemble full HTML ──
        html = f"""\
<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0;padding:0;font-family:'Segoe UI',Arial,Helvetica,sans-serif;background:#f0f2f5;">
<table width="100%" cellpadding="0" cellspacing="0">
<tr><td align="center" style="padding:20px 0;">
<table width="640" cellpadding="0" cellspacing="0"
       style="background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 4px 16px rgba(0,0,0,0.08);">
    <!-- Header -->
    <tr><td style="background:linear-gradient(135deg,#0b2545 0%,#13315c 100%);padding:28px 32px;">
        <table width="100%"><tr>
            <td><h1 style="margin:0;color:#ffffff;font-size:24px;font-weight:700;letter-spacing:-0.5px;">PROFEPA — Sistema APEX</h1>
                <p style="margin:6px 0 0;color:#8da9c4;font-size:13px;">Analisis Predictivo de Ecosistemas con IA</p></td>
            <td align="right" style="vertical-align:top;">
                <div style="background:rgba(255,255,255,0.12);border-radius:8px;padding:8px 14px;">
                    <span style="color:#8da9c4;font-size:10px;display:block;">FOLIO</span>
                    <span style="color:#fff;font-size:11px;font-weight:600;">{folio}</span>
                </div></td>
        </tr></table></td></tr>
    <!-- Type Badge + Date Range -->
    <tr><td style="padding:0;">
        <div style="background:{type_color}12;border-left:4px solid {type_color};padding:14px 32px;">
            <span style="font-size:13px;font-weight:600;color:{type_color};">{type_label}</span>
            <span style="font-size:12px;color:#6b7280;margin-left:12px;">{now_str}</span>
            {"<br><span style='font-size:11px;color:#6b7280;margin-top:4px;display:inline-block;'>Periodo analizado: " + date_range_str + "</span>" if date_range_str else ""}
        </div></td></tr>
    <!-- Summary KPIs -->
    <tr><td style="padding:24px 32px 16px;">
        <h2 style="margin:0 0 16px;color:#0b2545;font-size:20px;font-weight:700;">{display_name}</h2>
        <table width="100%"><tr>
            <td width="33%" style="padding-right:6px;">
                <div style="background:#f8f9fa;border-radius:10px;padding:16px;text-align:center;">
                    <div style="font-size:24px;font-weight:700;color:#0b2545;">{len(engines_analyzed)}</div>
                    <div style="font-size:10px;color:#6b7280;text-transform:uppercase;">Motores</div>
                </div></td>
            <td width="33%" style="padding:0 3px;">
                <div style="background:#f8f9fa;border-radius:10px;padding:16px;text-align:center;">
                    <div style="font-size:24px;font-weight:700;color:#0b2545;">{total_alerts}</div>
                    <div style="font-size:10px;color:#6b7280;text-transform:uppercase;">Detecciones</div>
                </div></td>
            <td width="33%" style="padding-left:6px;">
                <div style="background:{risk_bg};border-radius:10px;padding:16px;text-align:center;border:1px solid {risk_color}22;">
                    <div style="font-size:24px;font-weight:700;color:{risk_color};">{total_ha:.2f}</div>
                    <div style="font-size:10px;color:#6b7280;text-transform:uppercase;">Hectareas</div>
                </div></td>
        </tr></table>
        <div style="margin-top:12px;text-align:center;">
            <span style="display:inline-block;background:{risk_bg};border:1px solid {risk_color}33;border-radius:20px;padding:6px 20px;font-size:12px;font-weight:600;color:{risk_color};">
                Nivel de riesgo: {risk_level}
            </span>
        </div>
    </td></tr>
    <!-- Geo Location -->
    <tr><td style="padding:8px 32px 16px;">{geo_section}</td></tr>
    <!-- Satellite Sources -->
    <tr><td style="padding:8px 32px 16px;">{sat_table}</td></tr>
    <!-- Engine Results -->
    <tr><td style="padding:8px 32px 24px;">
        <h3 style="margin:0 0 14px;color:#0b2545;font-size:15px;font-weight:600;border-bottom:2px solid #e5e7eb;padding-bottom:8px;">
            Resultados por Motor de Analisis
        </h3>
        {engine_sections}
    </td></tr>
    <!-- Recommendations -->
    <tr><td style="padding:8px 32px 16px;">
        <h3 style="margin:0 0 10px;color:#0b2545;font-size:15px;font-weight:600;border-bottom:2px solid #e5e7eb;padding-bottom:8px;">
            Recomendaciones
        </h3>
        {self._generate_recommendations_html(total_ha, risk_level, layers)}
    </td></tr>
    <!-- Institutional Follow-up -->
    <tr><td style="padding:8px 32px 16px;">{followup_section}</td></tr>
    <!-- Methodology -->
    <tr><td style="padding:0 32px 16px;">
        <div style="background:#f8f9fa;border-radius:8px;padding:14px 16px;border:1px solid #e5e7eb;">
            <span style="font-size:11px;font-weight:600;color:#374151;">Metodologia:</span>
            <span style="font-size:10px;color:#6b7280;display:block;margin-top:4px;line-height:1.5;">
                Analisis realizado mediante imagenes satelitales Sentinel-2 (ESA, 10m) y
                clasificacion Dynamic World (Google). Motores adicionales: Hansen GFC (UMD),
                GLAD/RADD (WUR), MODIS MCD64A1 (NASA), Sentinel-1 SAR (ESA),
                WRI Deforestation Drivers. Los datos son procesados con IA y validados
                cruzadamente con MapBiomas. Todos los resultados son indicativos y deben
                ser verificados en campo por personal autorizado de PROFEPA.
            </span>
        </div>
    </td></tr>
    <!-- Note -->
    <tr><td style="padding:0 32px 24px;">
        <div style="background:#eff6ff;border-radius:8px;padding:14px 16px;border:1px solid #bfdbfe;">
            <span style="font-size:12px;color:#1e40af;font-weight:600;">Nota:</span>
            <span style="font-size:12px;color:#1e40af;">
                Se adjunta el reporte completo en formato PDF. Para mayor detalle,
                acceda al sistema APEX y consulte los resultados del analisis con folio {folio}.
            </span>
        </div>
    </td></tr>
    <!-- Footer -->
    <tr><td style="background:linear-gradient(135deg,#0b2545 0%,#13315c 100%);padding:20px 32px;text-align:center;">
        <p style="margin:0 0 4px;color:#8da9c4;font-size:11px;">
            Este correo fue generado automaticamente por el sistema APEX.</p>
        <p style="margin:0;color:#5a7fa0;font-size:10px;">
            Subprocuraduria de Recursos Naturales / CEPVR — PROFEPA</p>
    </td></tr>
</table></td></tr></table>
</body></html>"""
        return html

    # ------------------------------------------------------------------
    # Engine-specific extra info
    # ------------------------------------------------------------------

    @staticmethod
    def _engine_extra_info(engine_name: str, stats: dict) -> str:
        extra = ""
        if engine_name == "hansen":
            loss_by_year = stats.get("loss_by_year", {})
            if loss_by_year:
                top3 = sorted(loss_by_year.items(), key=lambda x: x[1], reverse=True)[:3]
                year_items = ", ".join([f"{y}: {h:.1f} ha" for y, h in top3])
                extra = f"<div style='font-size:11px;color:#6b7280;margin-top:6px;'>Top anos: {year_items}</div>"
            conf = stats.get("confidence", 0)
            avg_tc = stats.get("avg_treecover_pct", 0)
            if conf or avg_tc:
                extra += f"<div style='font-size:11px;color:#6b7280;'>Confianza: {conf:.1%} | Cobertura promedio: {avg_tc}%</div>"
        elif engine_name == "alerts":
            glad_n = stats.get("glad_count", 0)
            radd_n = stats.get("radd_count", 0)
            confirmed = stats.get("confirmed_count", 0)
            extra = f"<div style='font-size:11px;color:#6b7280;margin-top:6px;'>GLAD: {glad_n} | RADD: {radd_n} | Confirmadas: {confirmed}</div>"
        elif engine_name == "fire":
            corr = stats.get("fire_related_deforestation_pct", 0)
            if corr:
                extra = f"<div style='font-size:11px;color:#6b7280;margin-top:6px;'>Correlacion incendio-deforestacion: {corr}%</div>"
        elif engine_name == "drivers":
            dominant = stats.get("dominant_driver", "")
            drivers = stats.get("drivers", {})
            if dominant:
                extra = f"<div style='font-size:11px;color:#6b7280;margin-top:6px;'>Driver dominante: {dominant}</div>"
            if drivers:
                driver_list = ", ".join([f"{k}: {v}%" for k, v in list(drivers.items())[:3]])
                extra += f"<div style='font-size:11px;color:#6b7280;'>{driver_list}</div>"
        elif engine_name == "vegetation":
            classes = stats.get("classes", {})
            if classes:
                class_list = ", ".join([f"{k}: {v}%" for k, v in sorted(classes.items(), key=lambda x: x[1], reverse=True)[:4]])
                extra = f"<div style='font-size:11px;color:#6b7280;margin-top:6px;'>{class_list}</div>"
        elif engine_name == "sar":
            high_conf = stats.get("high_confidence_count", 0)
            if high_conf:
                extra = f"<div style='font-size:11px;color:#6b7280;margin-top:6px;'>Alta confianza: {high_conf} detecciones</div>"
        elif engine_name == "legal_context":
            intersects = stats.get("intersects_anp", False)
            anp_names = stats.get("anp_names", [])
            if intersects:
                names_str = ", ".join(anp_names[:3]) if anp_names else "Si"
                extra = f"<div style='font-size:11px;color:#dc2626;font-weight:600;margin-top:6px;'>Intersecta ANP: {names_str}</div>"
            else:
                extra = f"<div style='font-size:11px;color:#2ea043;margin-top:6px;'>No intersecta Areas Naturales Protegidas</div>"
        elif engine_name == "crossval":
            agreement = stats.get("agreement_pct", 0)
            extra = f"<div style='font-size:11px;color:#6b7280;margin-top:6px;'>Acuerdo DW vs MapBiomas: {agreement}%</div>"
        return extra

    # ------------------------------------------------------------------
    # Risk computation
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_risk(total_ha: float):
        if total_ha > 50:
            return "Critico", "#dc2626", "#fef2f2"
        elif total_ha > 10:
            return "Alto", "#f87148", "#fff7ed"
        elif total_ha > 1:
            return "Moderado", "#f0883e", "#fffbeb"
        return "Bajo", "#2ea043", "#f0fdf4"

    # ------------------------------------------------------------------
    # Dynamic recommendations
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_recommendations_html(total_ha: float, risk_level: str, layers: dict) -> str:
        recs = []
        if risk_level == "Critico":
            recs.append(
                "Se recomienda activar protocolo de inspeccion inmediata en la zona afectada "
                "y notificar a la Subprocuraduria de Recursos Naturales."
            )
            recs.append(
                "Considerar solicitar apoyo de la Guardia Nacional para resguardo "
                "del area mientras se realiza la verificacion en campo."
            )
        elif risk_level == "Alto":
            recs.append(
                "Programar visita de inspeccion en los proximos 7 dias habiles "
                "para confirmar los hallazgos satelitales."
            )
        elif risk_level == "Moderado":
            recs.append(
                "Mantener monitoreo continuo del area y programar verificacion "
                "en campo dentro de los proximos 15 dias."
            )
        else:
            recs.append(
                "Los niveles de cambio detectados se encuentran dentro de parametros normales. "
                "Continuar con el monitoreo periodico programado."
            )

        if "deforestation" in layers:
            ha = layers["deforestation"].get("stats", {}).get("area_ha", 0)
            if ha > 5:
                recs.append(
                    f"Deforestacion detectada: {ha:.1f} ha. Verificar si existe autorizacion "
                    "de cambio de uso de suelo (CUSTF) vigente para esta zona."
                )
        if "fire" in layers:
            burned = layers["fire"].get("stats", {}).get("total_burned_ha", 0)
            if burned > 0:
                recs.append(
                    f"Area quemada detectada: {burned:.1f} ha. Coordinar con CONAFOR "
                    "para determinar si el incendio fue intencional y evaluar dano ambiental."
                )
        if "legal_context" in layers:
            intersects = layers["legal_context"].get("stats", {}).get("intersects_anp", False)
            if intersects:
                anp_names = layers["legal_context"].get("stats", {}).get("anp_names", [])
                names_str = ", ".join(anp_names[:2]) if anp_names else "ANP"
                recs.append(
                    f"El area analizada intersecta con {names_str}. "
                    "Cualquier cambio detectado podria constituir un delito ambiental federal. "
                    "Notificar a la CONANP y evaluar procedimiento penal."
                )
        if "urban_expansion" in layers:
            ue_ha = layers["urban_expansion"].get("stats", {}).get("area_ha", 0)
            if ue_ha > 2:
                recs.append(
                    f"Expansion urbana detectada: {ue_ha:.1f} ha. Verificar permisos "
                    "de construccion y compatibilidad con uso de suelo municipal."
                )
        if not recs:
            recs.append("Sin recomendaciones adicionales para este analisis.")

        items = ""
        for i, rec in enumerate(recs[:5], 1):
            items += f"""
            <div style="display:flex;margin-bottom:8px;">
                <span style="min-width:22px;height:22px;background:#0b2545;color:#fff;border-radius:50%;
                             font-size:10px;font-weight:700;text-align:center;line-height:22px;margin-right:10px;">{i}</span>
                <span style="font-size:12px;color:#374151;line-height:1.5;">{rec}</span>
            </div>"""
        return items

    # ------------------------------------------------------------------
    # PDF generation
    # ------------------------------------------------------------------

    def _generate_report_pdf(
        self,
        job_id: str,
        results: dict,
        analysis_type: str = "manual",
        area_name: Optional[str] = None,
        date_range: Optional[List[str]] = None,
        geo: Optional[dict] = None,
        aoi_geojson: Optional[dict] = None,
    ) -> Optional[str]:
        """Generate PDF report using the full APEXPDFReportGenerator (maps, charts, satellite).

        Falls back to a basic text-only PDF if the enhanced generator fails.
        """
        # ── Try enhanced report generator first ──
        try:
            from ..modules.report_generator import APEXPDFReportGenerator

            # Build summary dict from results
            summary: dict = {}

            # Extract timeline_summary if pipeline stored it
            ts = results.get("timeline_summary", {})
            ts_geojson = ts.get("geojson", {}) if isinstance(ts, dict) else {}
            if isinstance(ts_geojson, dict) and (
                "timeline" in ts_geojson or "cumulative" in ts_geojson
            ):
                summary = dict(ts_geojson)

            # Ensure minimum structure
            summary.setdefault("timeline", {})
            summary.setdefault("cumulative", {})
            summary.setdefault("anomalies", [])

            # Add date_range / period
            if date_range and len(date_range) >= 2:
                summary["date_range"] = date_range
                summary["cumulative"].setdefault(
                    "period", f"{date_range[0]} — {date_range[1]}"
                )

            # Compute aggregate area from engine results
            total_ha = 0.0
            for eng, data in results.items():
                if eng == "timeline_summary":
                    continue
                stats = data.get("stats", {}) if isinstance(data, dict) else {}
                ha = (
                    stats.get("area_ha")
                    or stats.get("total_loss_ha")
                    or stats.get("total_burned_ha")
                    or stats.get("total_area_ha")
                    or stats.get("total_change_ha")
                    or 0
                )
                total_ha += float(ha or 0)
            summary["cumulative"].setdefault("total_area_ha", total_ha)

            # Merge extra engine results so section builders can find them
            extra_engines = (
                "hansen", "alerts", "drivers", "fire",
                "sar", "crossval", "legal_context",
            )
            for eng in extra_engines:
                if eng in results and eng not in summary:
                    data = results[eng]
                    geojson_data = data.get("geojson", {}) if isinstance(data, dict) else {}
                    stats_data = data.get("stats", {}) if isinstance(data, dict) else {}
                    merged = {
                        **(geojson_data if isinstance(geojson_data, dict) else {}),
                        **(stats_data if isinstance(stats_data, dict) else {}),
                    }
                    summary[eng] = merged

            # Generate PDF via the full report generator (includes maps)
            gen = APEXPDFReportGenerator()
            buf = gen.generate(summary, job_id, aoi_geojson=aoi_geojson)

            tmp = tempfile.NamedTemporaryFile(
                suffix=".pdf", prefix=f"APEX_{job_id[:8]}_",
                delete=False, dir=tempfile.gettempdir(),
            )
            tmp.write(buf.read())
            tmp.close()

            logger.info("Enhanced PDF report generated: %s", tmp.name)
            return tmp.name

        except Exception as exc:
            logger.error(
                "Enhanced PDF generation failed, falling back to basic: %s",
                exc, exc_info=True,
            )

        # ── Fallback: basic text-only PDF ──
        return self._generate_report_pdf_basic(
            job_id, results, analysis_type, area_name, date_range, geo,
        )

    def _generate_report_pdf_basic(
        self,
        job_id: str,
        results: dict,
        analysis_type: str = "manual",
        area_name: Optional[str] = None,
        date_range: Optional[List[str]] = None,
        geo: Optional[dict] = None,
    ) -> Optional[str]:
        try:
            from reportlab.lib import colors
            from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
            from reportlab.lib.pagesizes import letter
            from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
            from reportlab.lib.units import cm
            from reportlab.platypus import (
                SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
            )
        except ImportError:
            logger.warning("reportlab not installed — skipping PDF generation")
            return None

        try:
            geo = geo or {}
            folio = f"PROFEPA-APEX-{job_id[:8].upper()}"
            now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
            display_name = area_name or "Area de Interes"

            date_range_str = ""
            t2_start = t2_end = t1_start = t1_end = ""
            if date_range and len(date_range) >= 2:
                t2_start, t2_end = date_range[0], date_range[1]
                date_range_str = f"Periodo: {t2_start} — {t2_end}"
                try:
                    dt = datetime.strptime(t2_start, "%Y-%m-%d")
                    t1_start = (dt - timedelta(days=365)).strftime("%Y-%m-%d")
                    t1_end = t2_start
                except Exception:
                    pass

            type_label = {
                "manual": "Analisis de Deteccion",
                "monitoring": "Monitoreo Automatico",
                "timeline": "Analisis Multi-temporal",
            }.get(analysis_type, "Analisis")

            tmp = tempfile.NamedTemporaryFile(
                suffix=".pdf", prefix=f"APEX_{job_id[:8]}_",
                delete=False, dir=tempfile.gettempdir()
            )
            tmp_path = tmp.name
            tmp.close()

            doc = SimpleDocTemplate(
                tmp_path, pagesize=letter,
                leftMargin=2 * cm, rightMargin=2 * cm,
                topMargin=2 * cm, bottomMargin=2 * cm,
            )

            styles = getSampleStyleSheet()
            title_style = ParagraphStyle(
                "APEXTitle", parent=styles["Heading1"],
                fontSize=20, textColor=colors.HexColor("#0b2545"), spaceAfter=6,
            )
            subtitle_style = ParagraphStyle(
                "APEXSubtitle", parent=styles["Normal"],
                fontSize=11, textColor=colors.HexColor("#6b7280"), spaceAfter=14,
            )
            heading_style = ParagraphStyle(
                "APEXHeading", parent=styles["Heading2"],
                fontSize=14, textColor=colors.HexColor("#0b2545"),
                spaceBefore=16, spaceAfter=8,
            )
            body_style = ParagraphStyle(
                "APEXBody", parent=styles["Normal"],
                fontSize=10, textColor=colors.HexColor("#374151"),
                alignment=TA_JUSTIFY, spaceAfter=6,
            )
            small_style = ParagraphStyle(
                "APEXSmall", parent=styles["Normal"],
                fontSize=8, textColor=colors.HexColor("#6b7280"), spaceAfter=4,
            )

            elements = []

            # Title
            elements.append(Paragraph("PROFEPA — Sistema APEX", title_style))
            subtitle_parts = [type_label, f"Folio: {folio}", f"Fecha: {now_str}"]
            if date_range_str:
                subtitle_parts.append(date_range_str)
            elements.append(Paragraph(" | ".join(subtitle_parts), subtitle_style))
            elements.append(Spacer(1, 10))

            # Area name
            elements.append(Paragraph(f"Area: {display_name}", heading_style))

            # ── Geo info ──
            if geo:
                clat = geo.get("centroid_lat", "?")
                clon = geo.get("centroid_lon", "?")
                area_km2 = geo.get("area_km2", 0)
                bbox = geo.get("bbox", "?")
                gm_link = geo.get("google_maps", "")
                ge_link = geo.get("google_earth", "")

                elements.append(Paragraph("Localizacion", heading_style))
                geo_data = [
                    ["Centroide", f"Lat: {clat}  |  Lon: {clon}"],
                    ["Superficie", f"{area_km2:.2f} km2"],
                    ["Bounding Box", bbox],
                    ["Datum", "WGS84 / EPSG:4326"],
                    ["Google Maps", gm_link],
                    ["Google Earth", ge_link],
                ]
                gt = Table(geo_data, colWidths=[4 * cm, 12.5 * cm])
                gt.setStyle(TableStyle([
                    ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#374151")),
                    ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.HexColor("#f8f9fa"), colors.white]),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]))
                elements.append(gt)
                elements.append(Spacer(1, 8))

            # ── Summary results table ──
            layers = results if isinstance(results, dict) else {}
            summary_data = [["Motor", "Detecciones", "Hectareas", "Fuente"]]

            total_ha_pdf = 0.0
            for engine_name, data in layers.items():
                if engine_name in ("timeline_summary",):
                    continue
                stats = data.get("stats", data) if isinstance(data, dict) else {}
                geojson_data = data.get("geojson", {}) if isinstance(data, dict) else {}

                label = ENGINE_LABELS.get(engine_name, engine_name)
                ha = (stats.get("area_ha", 0) or stats.get("total_loss_ha", 0)
                      or stats.get("total_burned_ha", 0) or stats.get("total_area_ha", 0)
                      or stats.get("total_change_ha", 0) or 0)
                features = (stats.get("n_features", 0) or stats.get("total_alerts", 0)
                           or stats.get("fire_count", 0) or stats.get("sar_change_count", 0)
                           or stats.get("count", 0) or len(geojson_data.get("features", [])))
                source = stats.get("source", "GEE")
                total_ha_pdf += ha

                summary_data.append([label, str(features), f"{ha:.2f}", source[:30]])

            if len(summary_data) > 1:
                elements.append(Paragraph("Resumen de Resultados", heading_style))
                t = Table(summary_data, colWidths=[5.5 * cm, 3 * cm, 3 * cm, 5 * cm])
                t.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0b2545")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, 0), 9),
                    ("FONTSIZE", (0, 1), (-1, -1), 8),
                    ("ALIGN", (1, 0), (2, -1), "CENTER"),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8f9fa")]),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]))
                elements.append(t)
                elements.append(Spacer(1, 12))

            # ── Satellite sources table ──
            elements.append(Paragraph("Imagenes Satelitales Utilizadas", heading_style))
            sat_data = [["Sensor", "Periodo T1", "Periodo T2", "Resolucion"]]
            sat_data.append(["Sentinel-2 (ESA)", f"{t1_start} - {t1_end}" if t1_start else "N/A", f"{t2_start} - {t2_end}" if t2_start else "N/A", "10m optico"])
            sat_data.append(["Dynamic World (Google)", f"{t1_start} - {t1_end}" if t1_start else "N/A", f"{t2_start} - {t2_end}" if t2_start else "N/A", "10m DL"])
            sat_data.append(["Hansen GFC v1.12 (UMD)", "2000 - 2024", "Serie historica", "30m Landsat"])
            sat_data.append(["GLAD-S2 / RADD (WUR)", "Alertas tiempo real", "Semanal", "10m SAR+optico"])
            sat_data.append(["MODIS MCD64A1 (NASA)", "Areas quemadas", "Ultimos 12 meses", "500m termal"])
            sat_data.append(["Sentinel-1 SAR (ESA)", f"{t1_start} - {t1_end}" if t1_start else "N/A", f"{t2_start} - {t2_end}" if t2_start else "N/A", "10m radar"])

            st = Table(sat_data, colWidths=[5 * cm, 4 * cm, 4 * cm, 3.5 * cm])
            st.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0b2545")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8f9fa")]),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]))
            elements.append(st)
            elements.append(Spacer(1, 10))

            # ── Engine details ──
            for engine_name, data in layers.items():
                if engine_name in ("timeline_summary",):
                    continue
                stats = data.get("stats", data) if isinstance(data, dict) else {}
                label = ENGINE_LABELS.get(engine_name, engine_name)

                elements.append(Paragraph(label, heading_style))
                detail_lines = []
                for key, val in stats.items():
                    if key in ("n_groups",):
                        continue
                    if isinstance(val, dict):
                        detail_lines.append(f"<b>{key}:</b> " + ", ".join([f"{k}: {v}" for k, v in list(val.items())[:5]]))
                    elif isinstance(val, (int, float)):
                        detail_lines.append(f"<b>{key}:</b> {val}")
                    elif isinstance(val, str):
                        detail_lines.append(f"<b>{key}:</b> {val}")

                for line in detail_lines[:8]:
                    elements.append(Paragraph(line, body_style))

            # ── Risk level ──
            risk_level, _, _ = self._compute_risk(total_ha_pdf)
            elements.append(Paragraph(f"Nivel de riesgo: {risk_level} ({total_ha_pdf:.2f} ha total)", heading_style))

            # ── Recommendations ──
            elements.append(Paragraph("Recomendaciones", heading_style))
            recs_html = self._generate_recommendations_html(total_ha_pdf, risk_level, layers)
            import re
            rec_texts = re.findall(r'line-height:1\.5;">(.*?)</span>', recs_html)
            for i, rec in enumerate(rec_texts, 1):
                elements.append(Paragraph(f"{i}. {rec}", body_style))

            # ── Institutional follow-up ──
            elements.append(Paragraph("Seguimiento Institucional", heading_style))
            elements.append(Paragraph(
                f"<b>INMEDIATO (0-48h):</b> Registrar incidencia en SIGG-PROFEPA con folio {folio}. "
                "Asignar inspector responsable. Verificar CUSTF vigente.", body_style))
            elements.append(Paragraph(
                "<b>CORTO PLAZO (1-7 dias):</b> Realizar visita de inspeccion. "
                "Solicitar documentacion al propietario. Tomar evidencia fotografica con coordenadas.", body_style))
            elements.append(Paragraph(
                f"<b>EXPEDIENTE:</b> Adjuntar este reporte al expediente PROFEPA. "
                f"Referencia de imagenes: Sentinel-2 periodo {date_range_str or 'N/A'}. "
                "Sistema de referencia: WGS84 / EPSG:4326.", body_style))

            # ── Chain of custody ──
            elements.append(Spacer(1, 8))
            elements.append(Paragraph("Cadena de Custodia", heading_style))
            cust_data = [
                ["Folio APEX", folio, "Generado", now_str],
                ["Job ID", job_id, "Tipo analisis", type_label],
                ["Nivel riesgo", risk_level, "Area total", f"{total_ha_pdf:.2f} ha"],
            ]
            if geo:
                cust_data.append(["Centroide", f"{geo.get('centroid_lat','?')}, {geo.get('centroid_lon','?')}", "Superficie", f"{geo.get('area_km2',0):.2f} km2"])

            ct = Table(cust_data, colWidths=[3.5 * cm, 5 * cm, 3.5 * cm, 4.5 * cm])
            ct.setStyle(TableStyle([
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.HexColor("#f3f4f6"), colors.white]),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]))
            elements.append(ct)

            # ── Methodology ──
            elements.append(Spacer(1, 10))
            elements.append(Paragraph("Metodologia", heading_style))
            elements.append(Paragraph(
                "Analisis realizado mediante imagenes satelitales Sentinel-2 (ESA, 10m) y "
                "clasificacion Dynamic World (Google). Motores adicionales: Hansen GFC (UMD), "
                "GLAD/RADD (WUR), MODIS MCD64A1 (NASA), Sentinel-1 SAR (ESA), "
                "WRI Deforestation Drivers. Los datos son procesados con IA y validados "
                "cruzadamente con MapBiomas. Todos los resultados son indicativos y deben "
                "ser verificados en campo por personal autorizado de PROFEPA.", body_style))

            # ── Footer ──
            elements.append(Spacer(1, 20))
            footer_style = ParagraphStyle(
                "APEXFooter", parent=styles["Normal"],
                fontSize=8, textColor=colors.HexColor("#9ca3af"), alignment=TA_CENTER,
            )
            elements.append(Paragraph(
                "Documento generado automaticamente por el Sistema APEX — PROFEPA", footer_style))
            elements.append(Paragraph(
                "Subprocuraduria de Recursos Naturales / CEPVR", footer_style))

            doc.build(elements)
            logger.info("PDF report generated: %s", tmp_path)
            return tmp_path

        except Exception as exc:
            logger.error("PDF generation error: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _send_sync(self, to_email, subject, body_html, attachment_path) -> None:
        msg = MIMEMultipart()
        msg["From"] = self.from_email
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body_html, "html"))

        if attachment_path and Path(attachment_path).is_file():
            with open(attachment_path, "rb") as fh:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(fh.read())
            encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition",
                f'attachment; filename="{Path(attachment_path).name}"',
            )
            msg.attach(part)

        with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            if self.smtp_user and self.smtp_pass:
                server.login(self.smtp_user, self.smtp_pass)
            server.sendmail(self.from_email, to_email, msg.as_string())

    def send_test_email(self, to_email: str, area_name: str = "Area de prueba"):
        """Send a test email to verify SMTP configuration."""
        if not self.smtp_user or not self.smtp_pass:
            raise ValueError("SMTP no configurado. Define SMTP_USER y SMTP_PASS en .env")

        now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
        html = f"""\
<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;font-family:'Segoe UI',Arial,sans-serif;background:#f0f2f5;">
<table width="100%"><tr><td align="center" style="padding:20px 0;">
<table width="640" style="background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 4px 16px rgba(0,0,0,0.08);">
  <tr><td style="background:linear-gradient(135deg,#0b2545,#13315c);padding:28px 32px;">
    <h1 style="margin:0;color:#fff;font-size:24px;">PROFEPA — Sistema APEX</h1>
    <p style="margin:6px 0 0;color:#8da9c4;font-size:13px;">Prueba de Notificacion</p>
  </td></tr>
  <tr><td style="padding:28px 32px;">
    <h2 style="margin:0 0 16px;color:#0b2545;">Configuracion verificada</h2>
    <p style="color:#374151;font-size:14px;line-height:1.6;">
      Este es un correo de prueba del sistema APEX de PROFEPA.<br><br>
      El area <strong>{area_name}</strong> ha sido registrada para monitoreo.
    </p>
    <div style="margin:24px 0;padding:20px;background:#f0fdf4;border-radius:10px;text-align:center;border:1px solid #bbf7d0;">
      <span style="font-size:36px;">&#9989;</span><br>
      <strong style="color:#15803d;font-size:15px;">Email configurado correctamente</strong>
      <p style="margin:8px 0 0;color:#6b7280;font-size:11px;">{now_str}</p>
    </div>
  </td></tr>
  <tr><td style="background:linear-gradient(135deg,#0b2545,#13315c);padding:20px 32px;text-align:center;">
    <p style="margin:0 0 4px;color:#8da9c4;font-size:11px;">Sistema APEX — Analisis Predictivo de Ecosistemas con IA</p>
    <p style="margin:0;color:#5a7fa0;font-size:10px;">Subprocuraduria de Recursos Naturales / CEPVR — PROFEPA</p>
  </td></tr>
</table></td></tr></table></body></html>"""

        subject = f"[APEX] Prueba de notificacion — {area_name}"
        self._send_sync(to_email, subject, html, None)
        logger.info("Test email sent to %s", to_email)
