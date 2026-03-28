"""
APEX Generative AI Service — Gemini/Vertex AI integration.

Generates:
  - Alert briefs with legal context
  - Weekly reports per subdelegation
  - Natural language summaries

Guardrails: The LLM NEVER asserts certainty about an illicit —
only probabilities + recommendations.
"""

import json
import logging
import hashlib
from datetime import datetime
from typing import Optional

logger = logging.getLogger("apex.genai")


# ── Legal reference templates ──
LEGAL_REFERENCES = {
    "anp": {
        "articles": "Art. 47-64 LGEEPA (Áreas Naturales Protegidas)",
        "sanctions": "Multa de 1,000 a 50,000 UMA; clausura temporal o definitiva",
        "authority": "PROFEPA con dictamen de CONANP",
    },
    "cus": {
        "articles": "Art. 117 LGEEPA; Art. 7, 58, 163 LGDFS",
        "sanctions": "Multa de 40 a 30,000 UMA; restauración obligatoria; denuncia penal si > 2 ha",
        "authority": "PROFEPA / SEMARNAT",
    },
    "tala": {
        "articles": "Art. 163 LGDFS; Art. 418-420 Código Penal Federal",
        "sanctions": "Prisión 1-9 años; multa 100-3,000 UMA; decomiso de equipo",
        "authority": "PROFEPA / Fiscalía Federal",
    },
    "fire": {
        "articles": "Art. 420 Bis CPF; NOM-015-SEMARNAT/SAGARPA-2007",
        "sanctions": "Prisión 2-10 años si zona forestal",
        "authority": "CONAFOR / PROFEPA",
    },
}


class GenerativeAIService:
    """Vertex AI Gemini integration for APEX briefs and reports."""

    def __init__(self):
        self._client = None
        self._cache = {}  # In-memory cache; Redis in production

    def _get_client(self):
        """Lazy-load Vertex AI client."""
        if self._client is None:
            try:
                from ..config import settings
                import vertexai
                from vertexai.generative_models import GenerativeModel

                vertexai.init(
                    project=settings.GCP_PROJECT_ID,
                    location=settings.GCP_LOCATION,
                )
                self._client = GenerativeModel(settings.GEMINI_MODEL)
                logger.info("Vertex AI initialized: %s", settings.GEMINI_MODEL)
            except Exception as e:
                logger.warning("Vertex AI not available: %s. Using template fallback.", e)
        return self._client

    def _cache_key(self, prompt: str) -> str:
        return hashlib.md5(prompt.encode()).hexdigest()

    def _generate(self, prompt: str) -> str:
        """Generate text with caching and guardrails."""
        key = self._cache_key(prompt)
        if key in self._cache:
            return self._cache[key]

        client = self._get_client()
        if client is None:
            return self._template_fallback(prompt)

        try:
            response = client.generate_content(
                prompt,
                generation_config={
                    "temperature": 0.3,
                    "max_output_tokens": 2048,
                },
            )
            text = response.text
            self._cache[key] = text
            return text
        except Exception as e:
            logger.error("Gemini generation failed: %s", e)
            return self._template_fallback(prompt)

    def _template_fallback(self, prompt: str) -> str:
        """Simple template-based response when Gemini is unavailable."""
        return (
            "⚠️ Servicio de IA generativa no disponible. "
            "Se muestra el análisis basado en datos de los motores de detección."
        )

    def generate_alert_brief(self, alert_data: dict) -> dict:
        """
        Generate a comprehensive brief for a high-priority alert.

        Args:
            alert_data: Dict with keys: h3_index, probabilities, engines_triggered,
                        municipio, estado, ecosistema, en_anp, nombre_anp, ci
        """
        # Determine legal context
        p = alert_data.get("probabilities", {})
        type_probs = {
            "tala": p.get("tala", 0),
            "cus_inmobiliario": p.get("cus_inmobiliario", 0),
            "frontera_agricola": p.get("frontera_agricola", 0),
        }
        primary_type = max(type_probs, key=type_probs.get)
        en_anp = alert_data.get("en_anp", False)

        # Select legal reference
        if en_anp:
            legal = LEGAL_REFERENCES["anp"]
        elif primary_type == "tala":
            legal = LEGAL_REFERENCES["tala"]
        else:
            legal = LEGAL_REFERENCES["cus"]

        # Build engines summary
        engines = alert_data.get("engines_triggered", [])
        engines_text = ", ".join(engines) if engines else "Ninguno específico"

        ci = alert_data.get("ci", 0.5)
        estado = alert_data.get("estado", "No determinado")
        municipio = alert_data.get("municipio", "No determinado")
        ecosistema = alert_data.get("ecosistema", "No determinado")
        nombre_anp = alert_data.get("nombre_anp", "N/A")

        prompt = f"""Eres un analista de enforcement ambiental de PROFEPA/SEMARNAT. 
Genera un brief técnico de alerta con la siguiente información.

IMPORTANTE: Responde SIEMPRE en términos de probabilidades y recomendaciones.
NUNCA afirmes con certeza que hay un ilícito. Incluye siempre el Índice de Confianza (IC).

## Datos de la alerta:
- **Ubicación**: {municipio}, {estado}
- **Celda H3**: {alert_data.get('h3_index', 'N/A')}
- **Tipo de ecosistema**: {ecosistema}
- **En ANP**: {'Sí — ' + nombre_anp if en_anp else 'No'}
- **Índice de Confianza (IC)**: {ci:.2f}
- **Probabilidad de ilícito**: {sum(type_probs.values()):.1%}
  - Tala ilegal: {type_probs['tala']:.1%}
  - CUS Inmobiliario: {type_probs['cus_inmobiliario']:.1%}
  - Frontera agrícola: {type_probs['frontera_agricola']:.1%}
- **Motores que dispararon**: {engines_text}

## Marco legal aplicable:
- {legal['articles']}
- Sanciones: {legal['sanctions']}
- Autoridad competente: {legal['authority']}

Genera:
1. RESUMEN EJECUTIVO (2-3 oraciones)
2. HALLAZGOS DE LOS MOTORES (qué detectó cada motor)
3. MARCO LEGAL APLICABLE (artículos específicos)
4. CHECKLIST DE CAMPO (qué buscar en inspección)
5. RECOMENDACIÓN: INSPECCIÓN DIRECTA / ADQUIRIR IMAGEN PRIMERO / MONITOREAR"""

        generated_text = self._generate(prompt)

        # Build structured brief
        brief = {
            "alert_id": alert_data.get("h3_index", "unknown"),
            "generated_at": datetime.utcnow().isoformat(),
            "location": {
                "estado": estado,
                "municipio": municipio,
                "ecosistema": ecosistema,
                "en_anp": en_anp,
                "nombre_anp": nombre_anp,
            },
            "risk_assessment": {
                "confidence_index": ci,
                "p_illicit": round(sum(type_probs.values()), 4),
                "primary_type": primary_type,
                "probabilities": type_probs,
            },
            "legal_context": legal,
            "engines_triggered": engines,
            "brief_text": generated_text,
            "recommendation": (
                "INSPECCION_DIRECTA" if ci > 0.6 and sum(type_probs.values()) > 0.5
                else "ADQUIRIR_IMAGEN" if ci < 0.4
                else "MONITOREAR"
            ),
        }

        return brief

    def generate_weekly_report(
        self,
        subdelegacion_id: str,
        alerts: list[dict],
        stats: dict,
    ) -> dict:
        """Generate a weekly report for a subdelegation."""
        n_alerts = len(alerts)
        high_priority = sum(1 for a in alerts if a.get("p_illicit", 0) > 0.5)

        prompt = f"""Genera un reporte semanal de monitoreo ambiental para la subdelegación {subdelegacion_id}.

DATOS:
- Alertas activas: {n_alerts}
- Alertas de alta prioridad: {high_priority}
- Superficie total monitoreada: {stats.get('area_monitored_ha', 'N/A')} ha
- Daño acumulado estimado: {stats.get('total_damage_ha', 'N/A')} ha
- Tendencia vs semana anterior: {stats.get('trend', 'N/A')}

Genera:
1. RESUMEN EJECUTIVO
2. ALERTAS PRIORITARIAS (top 5)
3. RECOMENDACIONES DE INSPECCIÓN
4. MÉTRICAS COMPARATIVAS
5. ACCIONES SUGERIDAS PARA LA SEMANA"""

        report_text = self._generate(prompt)

        return {
            "subdelegacion_id": subdelegacion_id,
            "generated_at": datetime.utcnow().isoformat(),
            "period": f"Semana del {datetime.utcnow().strftime('%d/%m/%Y')}",
            "summary": {
                "total_alerts": n_alerts,
                "high_priority": high_priority,
                "area_monitored_ha": stats.get("area_monitored_ha"),
                "damage_ha": stats.get("total_damage_ha"),
            },
            "report_text": report_text,
            "top_alerts": alerts[:5],
        }


# Module-level singleton
genai_service = GenerativeAIService()
