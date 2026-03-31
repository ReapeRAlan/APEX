import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"), override=True)

class Settings:
    GEE_AUTH_MODE = os.getenv("GEE_AUTH_MODE", "interactive")
    GEE_SERVICE_ACCOUNT_EMAIL = os.getenv("GEE_SERVICE_ACCOUNT_EMAIL")
    GEE_KEY_FILE = os.getenv("GEE_KEY_FILE")
    BACKEND_PORT = int(os.getenv("BACKEND_PORT", 8008))
    DATA_DIR = os.getenv("DATA_DIR", "./data/tiles")
    DB_PATH = os.getenv("DB_PATH", "./db/apex.sqlite")
    TORCH_DEVICE = os.getenv("TORCH_DEVICE", "cuda")
    MAX_VRAM_GB = float(os.getenv("MAX_VRAM_GB", 5.5))
    INFERENCE_BATCH_SIZE = int(os.getenv("INFERENCE_BATCH_SIZE", 4))

    # Database (PostgreSQL preferred, SQLite fallback)
    DATABASE_URL = os.getenv("DATABASE_URL", "")

    # NASA FIRMS NRT Active Fire
    FIRMS_MAP_KEY = os.getenv("FIRMS_MAP_KEY", "")
    FIRMS_SOURCES = os.getenv(
        "FIRMS_SOURCES", "VIIRS_SNPP_NRT,VIIRS_NOAA20_NRT,VIIRS_NOAA21_NRT"
    ).split(",")

    # Monitoring / Email alerts
    SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
    SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
    SMTP_USER = os.getenv("SMTP_USER", "")
    SMTP_PASS = os.getenv("SMTP_PASS", "")
    ALERT_FROM_EMAIL = os.getenv("ALERT_FROM_EMAIL", os.getenv("SMTP_USER", ""))

    # Redis (for Celery task queue / caching)
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # Vertex AI / Gemini
    GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID", "")
    GCP_LOCATION = os.getenv("GCP_LOCATION", "us-central1")
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-pro")

    # Auth (JWT)
    SECRET_KEY = os.getenv("SECRET_KEY", "")
    JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
    JWT_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", 8))

    # MLflow
    MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000")

    # HuggingFace
    HF_TOKEN = os.getenv("HF_TOKEN", "")

settings = Settings()
