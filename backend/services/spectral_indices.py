import numpy as np
import rasterio
from pathlib import Path
from dataclasses import dataclass


@dataclass
class SpectralBands:
    B2: np.ndarray   # Azul
    B3: np.ndarray   # Verde
    B4: np.ndarray   # Rojo
    B5: np.ndarray   # Red-Edge 1
    B6: np.ndarray   # Red-Edge 2
    B7: np.ndarray   # Red-Edge 3
    B8: np.ndarray   # NIR
    B8A: np.ndarray  # NIR narrow
    B11: np.ndarray  # SWIR 1
    B12: np.ndarray  # SWIR 2
    transform: object
    crs: object

    @classmethod
    def from_raster(cls, path: Path) -> "SpectralBands":
        with rasterio.open(path) as src:
            descs = [d.upper() if d else "" for d in (src.descriptions or [])]
            def gb(name, fallback):
                for i, d in enumerate(descs):
                    if name in d:
                        b = src.read(i + 1).astype(np.float32)
                        return b / 10000.0 if np.nanmax(np.abs(b)) > 2.0 else b
                b = src.read(min(fallback, src.count)).astype(np.float32)
                return b / 10000.0 if np.nanmax(np.abs(b)) > 2.0 else b
            return cls(
                B2=gb("B2", 1), B3=gb("B3", 2), B4=gb("B4", 3),
                B5=gb("B5", 4), B6=gb("B6", 5), B7=gb("B7", 6),
                B8=gb("B8", 7), B8A=gb("B8A", 8),
                B11=gb("B11", 9), B12=gb("B12", 10),
                transform=src.transform, crs=src.crs,
            )


def safe_div(a, b, eps=1e-10):
    return a / (b + eps)


class SpectralIndices:
    """Calcula los 15 indices espectrales de Sentinel-2 para APEX."""

    @staticmethod
    def compute_all(b: SpectralBands) -> dict:
        # ---- Vegetacion ----
        NDVI  = safe_div(b.B8 - b.B4, b.B8 + b.B4)
        EVI   = 2.5 * safe_div(b.B8 - b.B4, b.B8 + 6 * b.B4 - 7.5 * b.B2 + 1)
        SAVI  = 1.5 * safe_div(b.B8 - b.B4, b.B8 + b.B4 + 0.5)
        MSAVI = safe_div(2 * b.B8 + 1 - np.sqrt((2 * b.B8 + 1) ** 2 - 8 * (b.B8 - b.B4)), 2)
        GNDVI = safe_div(b.B8 - b.B3, b.B8 + b.B3)

        # ---- Red-Edge (bosques densos, biomasa) ----
        NDRE  = safe_div(b.B8 - b.B5, b.B8 + b.B5)
        CIre  = safe_div(b.B8, b.B7) - 1
        RECIg = safe_div(b.B7, b.B5) - 1

        # ---- Agua ----
        NDWI  = safe_div(b.B3 - b.B8, b.B3 + b.B8)
        MNDWI = safe_div(b.B3 - b.B11, b.B3 + b.B11)
        NDMI  = safe_div(b.B8 - b.B11, b.B8 + b.B11)

        # ---- Suelo desnudo ----
        BSI   = safe_div((b.B11 + b.B4) - (b.B8 + b.B2),
                         (b.B11 + b.B4) + (b.B8 + b.B2))
        NBI   = safe_div(b.B11 * b.B4, b.B8)

        # ---- Urbano/construido ----
        NDBI  = safe_div(b.B11 - b.B8, b.B11 + b.B8)

        # ---- Fuego y quema ----
        NBR   = safe_div(b.B8 - b.B12, b.B8 + b.B12)

        return dict(
            NDVI=NDVI, EVI=EVI, SAVI=SAVI, MSAVI=MSAVI, GNDVI=GNDVI,
            NDRE=NDRE, CIre=CIre, RECIg=RECIg,
            NDWI=NDWI, MNDWI=MNDWI, NDMI=NDMI,
            BSI=BSI, NBI=NBI, NDBI=NDBI, NBR=NBR,
        )

    @staticmethod
    def classify_vegetation(idx: dict) -> np.ndarray:
        """
        Clasificacion por reglas espectrales con 7 clases.
        Usa multiples indices para reducir ambiguedad.
        """
        H, W = idx["NDVI"].shape
        # 0=agua 1=bosque_denso 2=bosque_ralo 3=pastizal 4=suelo 5=urbano 6=quemado
        cls = np.full((H, W), 4, dtype=np.uint8)

        # Quemado/perturbado: NBR muy bajo + NDVI muy bajo
        cls[(idx["NBR"] < -0.2) & (idx["NDVI"] < 0.15)] = 6

        # Pastizal
        cls[(idx["NDVI"] > 0.1) & (idx["NDVI"] <= 0.35) & (idx["MNDWI"] <= 0.0)] = 3

        # Bosque ralo/matorral: NDVI medio, EVI confirma
        cls[(idx["NDVI"] > 0.35) & (idx["NDVI"] <= 0.5) & (idx["MNDWI"] <= 0.0)] = 2

        # Bosque denso: NDVI alto + NDRE confirma
        cls[(idx["NDVI"] > 0.5) & (idx["NDRE"] > 0.2) & (idx["MNDWI"] <= 0.0)] = 1

        # Suelo desnudo: BSI alto + NBI confirma
        cls[(idx["BSI"] > 0.0) & (idx["NBI"] > 0.5) & (idx["MNDWI"] <= 0.0) & (idx["NDBI"] <= 0.05)] = 4

        # Urbano: NDBI alto + NDVI bajo
        cls[(idx["NDBI"] > 0.05) & (idx["NDVI"] < 0.25)] = 5

        # Agua: MNDWI > 0.1
        cls[idx["MNDWI"] > 0.1] = 0

        return cls

    @staticmethod
    def deforestation_mask(idx: dict) -> tuple:
        """
        Deteccion de suelo expuesto / deforestado usando multiples indices.
        Retorna mascara booleana y array de confianza [0-1].
        Sin change-detection temporal (single-date).
        """
        # Suelo expuesto: NDVI bajo, no agua, no urbano
        low_veg = idx["NDVI"] < 0.2
        not_water = idx["MNDWI"] <= 0.1
        not_urban = idx["NDBI"] <= 0.05
        not_deep_water = idx["NDVI"] > -0.3

        mask = low_veg & not_water & not_urban & not_deep_water

        # Confianza basada en multiples indices
        confidence = np.zeros_like(idx["NDVI"])
        confidence[mask] = 0.50
        confidence[mask & (idx["BSI"] > 0.0)]  += 0.15   # suelo confirmado
        confidence[mask & (idx["SAVI"] < 0.15)] += 0.10   # poca vegetacion
        confidence[mask & (idx["NBR"] < 0.1)]   += 0.10   # sin biomasa
        confidence[mask & (idx["EVI"] < 0.15)]   += 0.10  # EVI confirma
        confidence[mask & (idx["NDRE"] < 0.1)]   += 0.05  # red-edge confirma

        return mask, np.clip(confidence, 0, 0.95)