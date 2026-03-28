"""
APEX Celery Worker — Distributed task processing.

Queues:
  - gee: Google Earth Engine download tasks
  - gpu: PyTorch/CUDA inference tasks
  - default: General tasks
"""

import os
from celery import Celery

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "apex",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="America/Mexico_City",
    enable_utc=True,
    task_routes={
        "backend.worker.gee_*": {"queue": "gee"},
        "backend.worker.gpu_*": {"queue": "gpu"},
    },
)


@celery_app.task(name="backend.worker.gee_download")
def gee_download(job_id: str, aoi: dict, date_range: list):
    """Download satellite imagery from GEE."""
    from .services.gee_service import GEEService
    gee = GEEService()
    gee.initialize()
    return gee.get_sentinel2_composite(aoi, date_range[0], date_range[1], job_id=job_id)


@celery_app.task(name="backend.worker.gpu_inference")
def gpu_inference(job_id: str, raster_path: str, engine: str):
    """Run GPU-based model inference."""
    # Route to appropriate engine
    if engine == "prithvi":
        from .engines.prithvi_engine import PrithviEngine
        eng = PrithviEngine()
        return eng.analyze(raster_path)
    return {"error": f"Unknown GPU engine: {engine}"}
