"""
Polygon upload & conversion router.
Accepts GeoJSON, KML, KMZ, GPX, Shapefile (ZIP), WKT and returns
normalised GeoJSON geometry for use as AOI or reference layer.
"""

import io
import json
import logging
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, UploadFile, Form
from pydantic import BaseModel

logger = logging.getLogger("apex.polygons")
router = APIRouter()

# Max upload size: 50 MB
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

# Allowed extensions
ALLOWED_EXTENSIONS = {
    ".geojson", ".json",   # GeoJSON
    ".kml",                # KML
    ".kmz",                # KMZ (zipped KML)
    ".gpx",                # GPX
    ".zip",                # Shapefile zip
    ".wkt",                # Well-Known Text
    ".shp",                # Shapefile (standalone — need .shx/.dbf too)
}


def _geojson_to_polygons(data: dict) -> list[dict]:
    """Extract polygon geometries from any GeoJSON structure."""
    polygons = []
    geom_type = data.get("type", "")

    if geom_type == "FeatureCollection":
        for feat in data.get("features", []):
            polygons.extend(_geojson_to_polygons(feat))
    elif geom_type == "Feature":
        geom = data.get("geometry", {})
        if geom:
            polygons.extend(_geojson_to_polygons(geom))
    elif geom_type == "Polygon":
        polygons.append(data)
    elif geom_type == "MultiPolygon":
        for coords in data.get("coordinates", []):
            polygons.append({"type": "Polygon", "coordinates": coords})
    elif geom_type == "GeometryCollection":
        for g in data.get("geometries", []):
            polygons.extend(_geojson_to_polygons(g))

    return polygons


def _convert_with_geopandas(file_path: str, driver: Optional[str] = None) -> dict:
    """Use geopandas/fiona to read a geospatial file and return GeoJSON dict."""
    import geopandas as gpd

    kwargs = {}
    if driver:
        kwargs["driver"] = driver

    gdf = gpd.read_file(file_path, **kwargs)

    if gdf.empty:
        raise ValueError("El archivo no contiene geometrias")

    # Reproject to EPSG:4326 if needed
    if gdf.crs and not gdf.crs.is_geographic:
        gdf = gdf.to_crs(epsg=4326)
    elif gdf.crs is None:
        logger.warning("No CRS found — assuming EPSG:4326")

    # Filter only polygon geometries
    gdf = gdf[gdf.geometry.type.isin(["Polygon", "MultiPolygon"])]
    if gdf.empty:
        raise ValueError("No se encontraron poligonos en el archivo")

    return json.loads(gdf.to_json())


def _parse_wkt(wkt_text: str) -> dict:
    """Parse WKT text into GeoJSON geometry."""
    from shapely import wkt
    from shapely.geometry import mapping

    geom = wkt.loads(wkt_text.strip())
    return mapping(geom)


def _process_upload(content: bytes, filename: str) -> dict:
    """
    Process uploaded file bytes and return a dict with:
    - polygons: list of GeoJSON Polygon geometries
    - feature_collection: full GeoJSON FeatureCollection
    - bbox: [minLng, minLat, maxLng, maxLat]
    - count: number of polygons
    """
    ext = Path(filename).suffix.lower()
    geojson_fc = None

    if ext in (".geojson", ".json"):
        try:
            data = json.loads(content.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ValueError(f"Error al parsear GeoJSON: {e}")
        polygons = _geojson_to_polygons(data)
        if not polygons:
            raise ValueError("No se encontraron poligonos en el GeoJSON")

    elif ext == ".wkt":
        text = content.decode("utf-8").strip()
        geom = _parse_wkt(text)
        polygons = _geojson_to_polygons(geom)
        if not polygons:
            raise ValueError("El WKT no contiene geometrias de tipo Polygon")

    elif ext == ".kml":
        with tempfile.NamedTemporaryFile(suffix=".kml", delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        try:
            geojson_fc = _convert_with_geopandas(tmp_path, driver="KML")
        finally:
            Path(tmp_path).unlink(missing_ok=True)
        polygons = _geojson_to_polygons(geojson_fc)

    elif ext == ".kmz":
        # KMZ = ZIP with a doc.kml inside
        with tempfile.TemporaryDirectory() as tmpdir:
            kmz_path = Path(tmpdir) / "upload.kmz"
            kmz_path.write_bytes(content)
            with zipfile.ZipFile(kmz_path, "r") as zf:
                kml_names = [n for n in zf.namelist() if n.lower().endswith(".kml")]
                if not kml_names:
                    raise ValueError("El archivo KMZ no contiene un archivo .kml")
                zf.extract(kml_names[0], tmpdir)
                kml_path = Path(tmpdir) / kml_names[0]
            geojson_fc = _convert_with_geopandas(str(kml_path), driver="KML")
        polygons = _geojson_to_polygons(geojson_fc)

    elif ext == ".gpx":
        with tempfile.NamedTemporaryFile(suffix=".gpx", delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        try:
            geojson_fc = _convert_with_geopandas(tmp_path, driver="GPX")
        finally:
            Path(tmp_path).unlink(missing_ok=True)
        polygons = _geojson_to_polygons(geojson_fc)

    elif ext == ".zip":
        # Shapefile ZIP
        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = Path(tmpdir) / "upload.zip"
            zip_path.write_bytes(content)
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(tmpdir)
            # Find .shp file
            shp_files = list(Path(tmpdir).rglob("*.shp"))
            if not shp_files:
                raise ValueError("No se encontro archivo .shp en el ZIP")
            geojson_fc = _convert_with_geopandas(str(shp_files[0]))
        polygons = _geojson_to_polygons(geojson_fc)

    elif ext == ".shp":
        raise ValueError(
            "Para subir Shapefiles, comprime los archivos (.shp, .shx, .dbf, .prj) "
            "en un ZIP y sube el ZIP."
        )
    else:
        raise ValueError(f"Formato no soportado: {ext}")

    if not polygons:
        raise ValueError("No se encontraron geometrias de tipo Polygon")

    # Build FeatureCollection
    features = []
    for i, geom in enumerate(polygons):
        features.append({
            "type": "Feature",
            "geometry": geom,
            "properties": {"index": i, "source_file": filename},
        })

    fc = {"type": "FeatureCollection", "features": features}

    # Calculate bbox
    all_coords = []
    for geom in polygons:
        for ring in geom.get("coordinates", []):
            all_coords.extend(ring)

    if all_coords:
        lngs = [c[0] for c in all_coords]
        lats = [c[1] for c in all_coords]
        bbox = [min(lngs), min(lats), max(lngs), max(lats)]
    else:
        bbox = None

    return {
        "polygons": polygons,
        "feature_collection": fc,
        "bbox": bbox,
        "count": len(polygons),
    }


@router.post("/polygons/upload")
async def upload_polygon(
    file: UploadFile = File(...),
    name: Optional[str] = Form(None),
):
    """
    Upload a polygon file (GeoJSON, KML, KMZ, GPX, Shapefile ZIP, WKT).
    Returns parsed polygons as GeoJSON.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No se proporciono archivo")

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Formato no soportado: {ext}. "
                   f"Formatos validos: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Archivo demasiado grande (max {MAX_UPLOAD_BYTES // (1024*1024)} MB)",
        )

    try:
        result = _process_upload(content, file.filename)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception("Error processing polygon upload: %s", e)
        raise HTTPException(
            status_code=500,
            detail=f"Error procesando archivo: {type(e).__name__}: {e}",
        )

    polygon_id = str(uuid.uuid4())[:8]
    display_name = name or Path(file.filename).stem

    logger.info(
        "Polygon uploaded: id=%s name=%s file=%s count=%d",
        polygon_id, display_name, file.filename, result["count"],
    )

    return {
        "id": polygon_id,
        "name": display_name,
        "filename": file.filename,
        "format": ext.lstrip(".").upper(),
        "polygon_count": result["count"],
        "polygons": result["polygons"],
        "feature_collection": result["feature_collection"],
        "bbox": result["bbox"],
    }


class ParseWKTRequest(BaseModel):
    wkt: str
    name: Optional[str] = None


@router.post("/polygons/parse-wkt")
async def parse_wkt_text(req: ParseWKTRequest):
    """Parse a WKT string and return GeoJSON polygons."""
    try:
        geom = _parse_wkt(req.wkt)
        polygons = _geojson_to_polygons(geom)
        if not polygons:
            raise ValueError("El WKT no contiene geometrias de tipo Polygon")
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    features = [
        {"type": "Feature", "geometry": g, "properties": {"index": i}}
        for i, g in enumerate(polygons)
    ]
    fc = {"type": "FeatureCollection", "features": features}

    # bbox
    all_coords = []
    for g in polygons:
        for ring in g.get("coordinates", []):
            all_coords.extend(ring)
    bbox = None
    if all_coords:
        lngs = [c[0] for c in all_coords]
        lats = [c[1] for c in all_coords]
        bbox = [min(lngs), min(lats), max(lngs), max(lats)]

    polygon_id = str(uuid.uuid4())[:8]
    return {
        "id": polygon_id,
        "name": req.name or "WKT",
        "format": "WKT",
        "polygon_count": len(polygons),
        "polygons": polygons,
        "feature_collection": fc,
        "bbox": bbox,
    }
