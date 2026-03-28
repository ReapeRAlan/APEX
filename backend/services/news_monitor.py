"""
APEX NLP News Monitor — Weak signal pipeline for environmental crime detection.

Scrapes/parses RSS feeds from regional Mexican news sources, classifies
relevance to environmental crimes, extracts geographic entities, and
feeds results into the Bayesian belief map as weak signals.
"""

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass, field

import feedparser
import httpx

logger = logging.getLogger("apex.news")

# ── Regional news sources (RSS feeds) ──
NEWS_SOURCES = {
    "Yucatan": [
        {"name": "Yucatán Al Momento", "url": "https://www.yucatanalamano.com/feed/", "type": "rss"},
        {"name": "Diario de Yucatán", "url": "https://www.yucatan.com.mx/rss", "type": "rss"},
    ],
    "Campeche": [
        {"name": "Tribuna Campeche", "url": "https://tribunacampeche.com/feed/", "type": "rss"},
    ],
    "Quintana Roo": [
        {"name": "Noticaribe", "url": "https://noticaribe.com.mx/feed/", "type": "rss"},
        {"name": "Novedades QR", "url": "https://sfrancisco.novedadesqroo.com/feed/", "type": "rss"},
    ],
    "Chiapas": [
        {"name": "e-consulta Chiapas", "url": "https://www.econsulta.com/chiapas/rss", "type": "rss"},
    ],
    "Oaxaca": [
        {"name": "Oaxaca Digital", "url": "https://www.oaxacadigital.info/feed/", "type": "rss"},
    ],
    "Chihuahua": [
        {"name": "El Heraldo de Chihuahua", "url": "https://www.elheraldodechihuahua.com.mx/rss/", "type": "rss"},
    ],
    "Jalisco": [
        {"name": "El Informador", "url": "https://www.informador.mx/rss/", "type": "rss"},
    ],
    "Michoacán": [
        {"name": "La Voz de Michoacán", "url": "https://www.lavozdemichoacan.com.mx/feed/", "type": "rss"},
    ],
}

# ── Environmental keywords for zero-shot classification ──
ENVIRONMENTAL_KEYWORDS = [
    "deforestación", "deforestacion", "tala ilegal", "tala clandestina",
    "tala inmoderada", "desmonte", "cambio de uso de suelo",
    "incendio forestal", "incendio provocado", "quema",
    "megaproyecto ilegal", "construcción ilegal", "edificación ilegal",
    "invasión de terreno", "invasion", "despojo",
    "área natural protegida", "ANP", "reserva natural",
    "daño ambiental", "delito ambiental", "ecocidio",
    "selva", "manglar", "humedal", "bosque",
    "PROFEPA", "SEMARNAT", "CONAFOR",
    "denuncia ambiental", "clausura", "multa ambiental",
    "lotificación", "lotificacion", "fraccionamiento ilegal",
    "permiso forestal", "aprovechamiento forestal",
]

# Pre-compile pattern for fast matching
_KEYWORD_PATTERN = re.compile(
    "|".join(re.escape(kw) for kw in ENVIRONMENTAL_KEYWORDS),
    re.IGNORECASE,
)


@dataclass
class NewsArticle:
    """A parsed and classified news article."""
    title: str
    url: str
    published: Optional[datetime] = None
    source_name: str = ""
    source_state: str = ""
    summary: str = ""
    relevance_score: float = 0.0
    matched_keywords: list = field(default_factory=list)
    extracted_locations: list = field(default_factory=list)
    geocoded_lat: Optional[float] = None
    geocoded_lng: Optional[float] = None
    h3_index: Optional[str] = None


class NewsMonitor:
    """
    Fetches environmental news from RSS feeds, classifies relevance,
    extracts geographic entities, and integrates with Bayesian fusion.
    """

    def __init__(self):
        self._nlp = None  # Lazy-load spaCy

    def _get_nlp(self):
        """Lazy-load spaCy model."""
        if self._nlp is None:
            try:
                import spacy
                self._nlp = spacy.load("es_core_news_lg")
                logger.info("spaCy model loaded: es_core_news_lg")
            except Exception as e:
                logger.warning("Could not load spaCy: %s. NER will be disabled.", e)
        return self._nlp

    def fetch_articles(self, max_age_hours: int = 48) -> list[NewsArticle]:
        """Fetch recent articles from all configured RSS feeds."""
        articles = []
        cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)

        for state, sources in NEWS_SOURCES.items():
            for source in sources:
                try:
                    feed = feedparser.parse(source["url"])
                    for entry in feed.entries[:20]:  # Max 20 per feed
                        # Parse publication date
                        pub_date = None
                        if hasattr(entry, "published_parsed") and entry.published_parsed:
                            pub_date = datetime(*entry.published_parsed[:6])

                        # Skip old articles
                        if pub_date and pub_date < cutoff:
                            continue

                        article = NewsArticle(
                            title=entry.get("title", ""),
                            url=entry.get("link", ""),
                            published=pub_date,
                            source_name=source["name"],
                            source_state=state,
                            summary=entry.get("summary", "")[:500],
                        )
                        articles.append(article)

                except Exception as e:
                    logger.warning("Failed to fetch %s: %s", source["name"], e)

        logger.info("Fetched %d articles from %d states.", len(articles), len(NEWS_SOURCES))
        return articles

    def classify_relevance(self, articles: list[NewsArticle]) -> list[NewsArticle]:
        """
        Score each article for environmental crime relevance.
        Uses keyword matching (zero-shot) — upgrade to fine-tuned model later.
        """
        relevant = []

        for article in articles:
            text = f"{article.title} {article.summary}".lower()
            matches = _KEYWORD_PATTERN.findall(text)

            if matches:
                # Score based on number of unique keyword matches
                unique_matches = list(set(m.lower() for m in matches))
                article.matched_keywords = unique_matches
                article.relevance_score = min(1.0, len(unique_matches) * 0.15)
                relevant.append(article)

        logger.info(
            "Classified %d/%d articles as relevant (%.0f%%).",
            len(relevant), len(articles),
            (len(relevant) / max(1, len(articles))) * 100,
        )
        return relevant

    def extract_locations(self, articles: list[NewsArticle]) -> list[NewsArticle]:
        """Extract geographic entities using spaCy NER."""
        nlp = self._get_nlp()
        if nlp is None:
            return articles

        for article in articles:
            text = f"{article.title}. {article.summary}"
            doc = nlp(text)

            locations = []
            for ent in doc.ents:
                if ent.label_ in ("LOC", "GPE"):
                    locations.append(ent.text)

            article.extracted_locations = locations

        return articles

    def geocode_articles(self, articles: list[NewsArticle]) -> list[NewsArticle]:
        """
        Geocode extracted locations.
        First tries against INEGI municipality table, then Nominatim.
        """
        for article in articles:
            if not article.extracted_locations:
                continue

            # Try the most specific location first
            for location in article.extracted_locations:
                coords = self._geocode_nominatim(location, article.source_state)
                if coords:
                    article.geocoded_lat, article.geocoded_lng = coords

                    # Convert to H3
                    try:
                        import h3
                        article.h3_index = h3.geo_to_h3(
                            coords[0], coords[1], 6
                        )
                    except Exception:
                        pass
                    break

        geocoded = sum(1 for a in articles if a.geocoded_lat is not None)
        logger.info("Geocoded %d/%d articles.", geocoded, len(articles))
        return articles

    def _geocode_nominatim(
        self, location: str, state_hint: str
    ) -> Optional[tuple[float, float]]:
        """Geocode a location string using Nominatim (OSM)."""
        try:
            query = f"{location}, {state_hint}, México"
            resp = httpx.get(
                "https://nominatim.openstreetmap.org/search",
                params={
                    "q": query,
                    "format": "json",
                    "limit": 1,
                    "countrycodes": "mx",
                },
                headers={"User-Agent": "APEX-Environmental-Monitor/1.0"},
                timeout=10,
            )
            results = resp.json()
            if results:
                return (float(results[0]["lat"]), float(results[0]["lon"]))
        except Exception as e:
            logger.debug("Nominatim geocode failed for '%s': %s", location, e)

        return None

    def integrate_with_beliefs(self, articles: list[NewsArticle]):
        """Feed geocoded articles into the Bayesian belief map as weak signals."""
        from .bayesian_fusion import bayesian_fusion

        integrated = 0
        for article in articles:
            if article.h3_index and article.relevance_score > 0.2:
                bayesian_fusion.update_beliefs(
                    motor_id="news_nlp",
                    h3_index=article.h3_index,
                    detection_probability=article.relevance_score,
                    confidence=0.15,  # Weak signal weight
                )
                integrated += 1

        logger.info("Integrated %d news articles into belief map.", integrated)
        return integrated

    def run_pipeline(self) -> dict:
        """Full pipeline: fetch → classify → extract → geocode → integrate."""
        logger.info("Starting news monitoring pipeline...")

        articles = self.fetch_articles()
        relevant = self.classify_relevance(articles)
        relevant = self.extract_locations(relevant)
        relevant = self.geocode_articles(relevant)
        integrated = self.integrate_with_beliefs(relevant)

        result = {
            "total_fetched": len(articles),
            "relevant": len(relevant),
            "geocoded": sum(1 for a in relevant if a.geocoded_lat),
            "integrated": integrated,
            "timestamp": datetime.utcnow().isoformat(),
            "top_articles": [
                {
                    "title": a.title,
                    "url": a.url,
                    "source": a.source_name,
                    "state": a.source_state,
                    "relevance": a.relevance_score,
                    "keywords": a.matched_keywords,
                    "locations": a.extracted_locations,
                    "h3": a.h3_index,
                }
                for a in sorted(relevant, key=lambda x: x.relevance_score, reverse=True)[:10]
            ],
        }

        logger.info("News pipeline complete: %s", json.dumps(result, default=str)[:500])
        return result


# Module-level singleton
news_monitor = NewsMonitor()
