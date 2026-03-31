"""
APEX Local Chat Service — Rule-based contextual AI assistant.

Provides intelligent Q&A over analysis results WITHOUT external APIs,
model downloads, or GPU usage. Fully local, zero cost.

Covers all 13 engines: deforestation, vegetation, structures, urban_expansion,
hansen, alerts, drivers, fire, sar, firms_hotspots, avocado, spectralgpt, drivers_mx.
"""
from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger("apex.local_chat")


def _fmt(v, decimals=1) -> str:
    if isinstance(v, (int, float)):
        return f"{v:,.{decimals}f}" if decimals else f"{v:,}"
    return str(v or "N/D")


def _build_engine_summaries(job_results: dict) -> dict:
    """Parse all engine results into structured summaries."""
    # Unwrap "layers" envelope from /api/results response
    if "layers" in job_results and isinstance(job_results["layers"], dict):
        job_results = job_results["layers"]
    s = {}

    if "deforestation" in job_results:
        st = job_results["deforestation"].get("stats", {})
        n = st.get("n_features", 0)
        ha = st.get("area_ha", 0)
        co2 = st.get("co2_tonnes", 0)
        agbd = st.get("mean_agbd_mg_ha", 0)
        s["deforestation"] = {
            "ha": ha, "n": n, "co2": co2, "agbd": agbd,
            "text": (f"Se detectaron {n} poligonos de deforestacion con un total de "
                     f"{_fmt(ha)} ha perdidas. "
                     + (f"Impacto estimado: {_fmt(co2)} toneladas de CO2 equivalente "
                        f"(biomasa media: {_fmt(agbd)} Mg/ha)." if co2 else "")),
        }

    if "vegetation" in job_results:
        st = job_results["vegetation"].get("stats", {})
        classes = st.get("classes", {})
        if classes:
            top = sorted(classes.items(), key=lambda x: x[1], reverse=True)[:6]
            cls_text = ", ".join(f"{k}: {v}%" for k, v in top)
            s["vegetation"] = {
                "classes": classes, "top": top,
                "text": f"Clasificacion de vegetacion (Dynamic World): {cls_text}.",
            }

    if "structures" in job_results:
        st = job_results["structures"].get("stats", {})
        n = st.get("n_features", 0)
        s["structures"] = {
            "n": n,
            "text": f"Se detectaron {n} estructuras o construcciones en el area.",
        }

    if "urban_expansion" in job_results:
        st = job_results["urban_expansion"].get("stats", {})
        ha = st.get("area_ha", 0)
        n = st.get("n_features", 0)
        s["urban_expansion"] = {
            "ha": ha, "n": n,
            "text": f"Expansion urbana detectada: {_fmt(ha)} ha en {n} zonas.",
        }

    if "hansen" in job_results:
        st = job_results["hansen"].get("stats", {})
        ha = st.get("total_loss_ha", st.get("loss_ha", 0))
        tc = st.get("avg_treecover_pct", 0)
        n = st.get("n_features", 0)
        s["hansen"] = {
            "ha": ha, "treecover": tc, "n": n,
            "text": (f"Hansen Global Forest Change: {_fmt(ha)} ha de perdida forestal, "
                     f"cobertura arbórea original promedio: {_fmt(tc, 0)}%."),
        }

    if "alerts" in job_results:
        st = job_results["alerts"].get("stats", {})
        total = st.get("total_alerts", 0)
        glad = st.get("glad_count", 0)
        radd = st.get("radd_count", 0)
        s["alerts"] = {
            "total": total, "glad": glad, "radd": radd,
            "text": f"Alertas de deforestacion: {total} total ({glad} GLAD, {radd} RADD).",
        }

    if "drivers" in job_results:
        st = job_results["drivers"].get("stats", {})
        n = st.get("n_features", 0)
        classes = st.get("driver_classes", {})
        s["drivers"] = {
            "n": n, "classes": classes,
            "text": f"Drivers de deforestacion (WRI): {n} zonas clasificadas.",
        }

    if "fire" in job_results:
        st = job_results["fire"].get("stats", {})
        ha = st.get("total_burned_ha", 0)
        count = st.get("fire_count", 0)
        s["fire"] = {
            "ha": ha, "count": count,
            "text": f"Incendios (MODIS): {_fmt(ha)} ha quemadas, {count} eventos detectados.",
        }

    if "sar" in job_results:
        st = job_results["sar"].get("stats", {})
        ha = st.get("total_change_ha", 0)
        n = st.get("n_features", 0)
        s["sar"] = {
            "ha": ha, "n": n,
            "text": f"Cambios SAR (Sentinel-1): {_fmt(ha)} ha con cambios significativos ({n} zonas).",
        }

    if "firms_hotspots" in job_results:
        st = job_results["firms_hotspots"].get("stats", {})
        count = st.get("hotspot_count", 0)
        frp = st.get("total_frp_mw", 0)
        hi = st.get("high_confidence_count", 0)
        s["firms_hotspots"] = {
            "count": count, "frp": frp, "high_conf": hi,
            "text": (f"FIRMS NRT: {count} hotspots detectados "
                     f"({hi} alta confianza, FRP total: {_fmt(frp)} MW)."),
        }

    if "avocado" in job_results:
        st = job_results["avocado"].get("stats", {})
        n = st.get("n_anomalies", 0)
        ha = st.get("total_area_ha", 0)
        s["avocado"] = {
            "n": n, "ha": ha,
            "text": f"Anomalias NDVI (AVOCADO): {n} zonas anomalas, {_fmt(ha)} ha afectadas.",
        }

    if "spectralgpt" in job_results:
        st = job_results["spectralgpt"].get("stats", {})
        classes = st.get("classes", {})
        if classes:
            top = sorted(classes.items(), key=lambda x: x[1], reverse=True)[:5]
            cls_text = ", ".join(f"{k}: {v}%" for k, v in top)
            s["spectralgpt"] = {
                "classes": classes,
                "text": f"SpectralGPT LULC: {cls_text}.",
            }

    if "drivers_mx" in job_results:
        st = job_results["drivers_mx"].get("stats", {})
        n = st.get("n_features", 0)
        s["drivers_mx"] = {
            "n": n,
            "text": f"ForestNet-MX: {n} zonas con drivers de deforestacion clasificados para Mexico.",
        }

    # Timeline summary
    if "timeline_summary" in job_results:
        tl = job_results["timeline_summary"]
        cum = tl.get("cumulative", {})
        years = tl.get("years", [])
        anomalies = tl.get("anomalies", [])
        s["timeline"] = {
            "years": years,
            "cumulative": cum,
            "anomalies": anomalies,
            "text": (
                f"Timeline {cum.get('period', '?')}: "
                f"deforestacion acumulada {_fmt(cum.get('total_deforestation_ha', 0))} ha, "
                f"expansion urbana {_fmt(cum.get('total_urban_expansion_ha', 0))} ha, "
                f"incendios {_fmt(cum.get('total_burned_ha', 0))} ha, "
                f"CO2 {_fmt(cum.get('total_co2_tonnes', 0))} t. "
                f"{len(anomalies)} anomalias detectadas."
            ),
        }

    return s


def _match_topic(question: str) -> list:
    """Identify which topics the question is about."""
    q = question.lower()
    topics = []

    topic_keywords = {
        "summary": ["resumen", "resúmen", "resuma", "summary", "general", "todo", "completo"],
        "deforestation": ["deforest", "bosque", "forest", "pérdida", "perdida", "tala"],
        "vegetation": ["vegetaci", "cobertura", "clase", "lulc", "uso de suelo"],
        "urban": ["urban", "expansion", "ciudad", "asentamiento", "construcc"],
        "hansen": ["hansen", "gfc", "global forest"],
        "alerts": ["alerta", "alert", "glad", "radd"],
        "drivers": ["driver", "causa", "motivo", "por qué", "porque"],
        "fire": ["incendio", "fuego", "fire", "quem", "burned"],
        "co2": ["co2", "carbono", "carbon", "biomasa", "biomass", "emisi"],
        "sar": ["sar", "sentinel-1", "radar", "microonda"],
        "firms": ["firms", "hotspot", "punto de calor", "nrt"],
        "avocado": ["avocado", "ndvi", "anomal"],
        "spectralgpt": ["spectralgpt", "spectral", "lulc"],
        "drivers_mx": ["forestnet", "forest-net", "drivers_mx", "mexico"],
        "timeline": ["timeline", "tendencia", "trend", "temporal", "evolu", "histori", "serie"],
        "recommendation": ["recomiend", "recomenda", "qué hacer", "accion", "sugier", "medida"],
        "risk": ["riesgo", "prioridad", "critico", "urgente", "grave"],
    }

    for topic, keywords in topic_keywords.items():
        if any(kw in q for kw in keywords):
            topics.append(topic)

    return topics if topics else ["summary"]


def chat_query(
    question: str,
    job_id: str = None,
    job_results: dict = None,
    image_base64: str = None,
) -> dict:
    """Answer a question about analysis results using local rule-based AI."""
    if not job_results:
        return {
            "answer": (
                "No hay datos de analisis disponibles aun. "
                "Ejecuta primero un analisis en la pestana correspondiente "
                "dibujando un poligono en el mapa y presionando 'Analizar AOI'."
            ),
            "model": "apex-local",
            "mode": "local",
            "context_used": False,
        }

    summaries = _build_engine_summaries(job_results)
    topics = _match_topic(question)

    if not summaries:
        return {
            "answer": (
                "El analisis se ejecuto pero no produjo resultados significativos. "
                "Esto puede deberse a que el area seleccionada no presenta cambios "
                "detectables o los datos satelitales no estan disponibles para esa zona/periodo."
            ),
            "model": "apex-local",
            "mode": "local",
            "context_used": False,
        }

    answer_parts = []

    # Summary
    if "summary" in topics:
        answer_parts.append("**Resumen del analisis:**\n")
        for key, data in summaries.items():
            if "text" in data:
                answer_parts.append(f"- {data['text']}")
        if not answer_parts[-1:] or answer_parts[-1] == "**Resumen del analisis:**\n":
            answer_parts.append("- No se detectaron cambios significativos en el area.")

    # Deforestation
    if "deforestation" in topics:
        d = summaries.get("deforestation")
        if d:
            answer_parts.append(f"\n**Deforestacion:**\n{d['text']}")
            if d.get("co2"):
                answer_parts.append(
                    f"El impacto climatico estimado es de {_fmt(d['co2'])} toneladas de CO2 "
                    f"equivalente, basado en datos de biomasa GEDI (NASA)."
                )
        else:
            answer_parts.append("\nNo se detecto deforestacion significativa en esta area.")

    # CO2 / Biomass
    if "co2" in topics:
        d = summaries.get("deforestation")
        if d and d.get("co2"):
            answer_parts.append(
                f"\n**Impacto en biomasa y CO2:**\n"
                f"- CO2 equivalente liberado: {_fmt(d['co2'])} toneladas\n"
                f"- Biomasa media (GEDI AGBD): {_fmt(d['agbd'])} Mg/ha\n"
                f"- Area deforestada: {_fmt(d['ha'])} ha"
            )
        else:
            answer_parts.append(
                "\nNo hay datos de biomasa/CO2 disponibles. "
                "Estos se calculan cuando se detecta deforestacion con el motor GEDI Biomass."
            )

    # Fire
    if "fire" in topics:
        parts = []
        f_data = summaries.get("fire")
        if f_data:
            parts.append(f_data["text"])
        firms = summaries.get("firms_hotspots")
        if firms:
            parts.append(firms["text"])
        if parts:
            answer_parts.append("\n**Incendios:**\n" + "\n".join(f"- {p}" for p in parts))
        else:
            answer_parts.append("\nNo se detectaron incendios significativos en esta area.")

    # Alerts
    if "alerts" in topics:
        a = summaries.get("alerts")
        if a:
            answer_parts.append(
                f"\n**Alertas de deforestacion:**\n{a['text']}\n"
                f"GLAD (Global Land Analysis & Discovery) detecta cambios en "
                f"la cobertura forestal usando Landsat. RADD (Radar Alerts for "
                f"Detecting Deforestation) usa Sentinel-1 SAR."
            )
        else:
            answer_parts.append("\nNo hay alertas GLAD/RADD vigentes para esta area.")

    # Hansen
    if "hansen" in topics:
        h = summaries.get("hansen")
        if h:
            answer_parts.append(f"\n**Hansen GFC:**\n{h['text']}")
        else:
            answer_parts.append("\nNo hay datos Hansen disponibles.")

    # Vegetation
    if "vegetation" in topics:
        v = summaries.get("vegetation")
        sp = summaries.get("spectralgpt")
        if v:
            answer_parts.append(f"\n**Vegetacion:**\n{v['text']}")
        if sp:
            answer_parts.append(f"\n**SpectralGPT LULC:**\n{sp['text']}")
        if not v and not sp:
            answer_parts.append("\nNo hay datos de vegetacion disponibles.")

    # Urban
    if "urban" in topics:
        u = summaries.get("urban_expansion")
        s = summaries.get("structures")
        parts = []
        if u:
            parts.append(u["text"])
        if s:
            parts.append(s["text"])
        if parts:
            answer_parts.append("\n**Expansion urbana:**\n" + "\n".join(f"- {p}" for p in parts))
        else:
            answer_parts.append("\nNo se detecto expansion urbana significativa.")

    # Drivers
    if "drivers" in topics:
        d = summaries.get("drivers")
        dm = summaries.get("drivers_mx")
        parts = []
        if d:
            parts.append(d["text"])
        if dm:
            parts.append(dm["text"])
        if parts:
            answer_parts.append(
                "\n**Causas de deforestacion:**\n" + "\n".join(f"- {p}" for p in parts)
            )
        else:
            answer_parts.append("\nNo hay datos de drivers/causas disponibles.")

    # SAR
    if "sar" in topics:
        s = summaries.get("sar")
        if s:
            answer_parts.append(
                f"\n**SAR Sentinel-1:**\n{s['text']}\n"
                f"El analisis SAR detecta cambios estructurales usando radar "
                f"de apertura sintetica, independiente de nubosidad."
            )
        else:
            answer_parts.append("\nNo hay datos SAR disponibles.")

    # FIRMS
    if "firms" in topics:
        f = summaries.get("firms_hotspots")
        if f:
            answer_parts.append(f"\n**FIRMS Hotspots:**\n{f['text']}")
        else:
            answer_parts.append("\nNo hay hotspots FIRMS para esta area.")

    # AVOCADO
    if "avocado" in topics:
        a = summaries.get("avocado")
        if a:
            answer_parts.append(
                f"\n**Anomalias NDVI (AVOCADO):**\n{a['text']}\n"
                f"AVOCADO detecta anomalias en la fenologia de la vegetacion "
                f"comparando series temporales de NDVI."
            )
        else:
            answer_parts.append("\nNo hay anomalias NDVI detectadas.")

    # SpectralGPT
    if "spectralgpt" in topics and "vegetation" not in topics:
        sp = summaries.get("spectralgpt")
        if sp:
            answer_parts.append(f"\n**SpectralGPT:**\n{sp['text']}")

    # ForestNet-MX
    if "drivers_mx" in topics and "drivers" not in topics:
        dm = summaries.get("drivers_mx")
        if dm:
            answer_parts.append(f"\n**ForestNet-MX:**\n{dm['text']}")

    # Timeline
    if "timeline" in topics:
        tl = summaries.get("timeline")
        if tl:
            answer_parts.append(f"\n**Analisis temporal:**\n{tl['text']}")
            anomalies = tl.get("anomalies", [])
            if anomalies:
                answer_parts.append("\nAnomalias detectadas:")
                for a in anomalies[:5]:
                    answer_parts.append(f"- Ano {a.get('year', '?')}: {a.get('description', a.get('type', '?'))}")
        else:
            answer_parts.append(
                "\nNo hay datos de timeline disponibles. "
                "Ejecuta un analisis Timeline para ver la evolucion temporal."
            )

    # Risk assessment
    if "risk" in topics:
        risk_items = []
        d = summaries.get("deforestation")
        if d and d.get("ha", 0) > 0:
            risk_items.append(f"Deforestacion activa: {_fmt(d['ha'])} ha (CRITICO)")
        f = summaries.get("firms_hotspots")
        if f and f.get("count", 0) > 0:
            risk_items.append(f"Puntos de calor activos: {f['count']} (URGENTE)")
        a = summaries.get("alerts")
        if a and a.get("total", 0) > 0:
            risk_items.append(f"Alertas vigentes: {a['total']} (ALTO)")
        u = summaries.get("urban_expansion")
        if u and u.get("ha", 0) > 0:
            risk_items.append(f"Expansion urbana: {_fmt(u['ha'])} ha (MEDIO)")
        if risk_items:
            answer_parts.append(
                "\n**Evaluacion de riesgo:**\n" + "\n".join(f"- {r}" for r in risk_items)
            )
        else:
            answer_parts.append("\nNo se identificaron riesgos significativos.")

    # Recommendations
    if "recommendation" in topics:
        recs = []
        d = summaries.get("deforestation")
        if d and d.get("ha", 0) > 10:
            recs.append("Verificar in situ las zonas de deforestacion detectadas e iniciar procedimiento de inspeccion.")
        f = summaries.get("firms_hotspots")
        if f and f.get("count", 0) > 5:
            recs.append("Coordinar con CONAFOR para atencion de incendios activos.")
        a = summaries.get("alerts")
        if a and a.get("total", 0) > 0:
            recs.append("Dar seguimiento a las alertas GLAD/RADD con imagenes recientes.")
        u = summaries.get("urban_expansion")
        if u and u.get("ha", 0) > 0:
            recs.append("Verificar cambios de uso de suelo contra permisos autorizados.")
        av = summaries.get("avocado")
        if av and av.get("n", 0) > 0:
            recs.append("Investigar causas de anomalias NDVI (posible degradacion, plagas o tala selectiva).")
        if recs:
            answer_parts.append(
                "\n**Recomendaciones:**\n" + "\n".join(f"{i+1}. {r}" for i, r in enumerate(recs))
            )
        else:
            recs.append("El area no presenta indicadores criticos. Continuar monitoreo periodico.")
            answer_parts.append(
                "\n**Recomendaciones:**\n1. " + recs[0]
            )

    answer = "\n".join(answer_parts).strip()
    if not answer:
        answer = (
            "No tengo informacion suficiente para responder esa pregunta especifica. "
            "Intenta con: '¿Cual es el resumen del analisis?', "
            "'¿Cuanta deforestacion se detecto?', o '¿Que recomendaciones hay?'"
        )

    return {
        "answer": answer,
        "model": "apex-local",
        "mode": "local",
        "context_used": True,
    }


def get_status() -> dict:
    """Return local chat service status."""
    return {
        "service": "apex-local-chat",
        "model": "apex-local",
        "loaded": True,
        "gpu_available": False,
        "gpu_name": None,
        "gpu_memory_gb": 0,
        "quantization": "N/A (rule-based)",
        "mode": "local",
        "description": "IA local sin dependencias externas",
    }


def unload_model():
    """No-op — no model to unload."""
    pass
