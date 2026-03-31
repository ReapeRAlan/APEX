import logging
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from .config import settings
from .routers import analysis, export, monitoring, polygons, grid, beliefs, pomdp, auth, strategic, kpi, forecast, chat
from .db.session import init_db, check_connection

# ── Logging setup ──
# Configure apex.* loggers — remove any existing handlers first
_apex_root = logging.getLogger("apex")
_apex_root.handlers.clear()
_apex_handler = logging.StreamHandler()
_apex_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s — %(message)s", datefmt="%H:%M:%S"
))
_apex_root.setLevel(logging.INFO)
_apex_root.addHandler(_apex_handler)
_apex_root.propagate = False  # prevent duplicate output via root/uvicorn logger

logger = logging.getLogger("apex.server")

app = FastAPI(title="APEX Backend API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For development
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log every HTTP request with method, path, status, and duration."""
    start = time.perf_counter()
    method = request.method
    path = request.url.path
    client = request.client.host if request.client else "?"

    # Log request entry
    logger.info(">> %s %s (client=%s)", method, path, client)

    response = await call_next(request)

    elapsed_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "<< %s %s -> %d (%.0fms)",
        method, path, response.status_code, elapsed_ms,
    )
    return response


app.include_router(analysis.router, prefix="/api")
app.include_router(export.router, prefix="/api")
app.include_router(monitoring.router, prefix="/api")
app.include_router(polygons.router, prefix="/api")
app.include_router(grid.router, prefix="/api")
app.include_router(beliefs.router, prefix="/api")
app.include_router(pomdp.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(strategic.router, prefix="/api")
app.include_router(kpi.router, prefix="/api")
app.include_router(forecast.router, prefix="/api")
app.include_router(chat.router, prefix="/api")


@app.on_event("startup")
async def startup_log():
    init_db()
    db_ok = check_connection()
    logger.info("=== APEX Backend starting on port %s ===", settings.BACKEND_PORT)
    logger.info("DATABASE_URL=%s | DB_OK=%s", settings.DATABASE_URL or "(sqlite fallback)", db_ok)
    logger.info("SMTP=%s:%s | SMTP_USER=%s",
                settings.SMTP_HOST, settings.SMTP_PORT, settings.SMTP_USER or "(not set)")


@app.get("/")
def read_root():
    return {"message": "APEX API running"}
