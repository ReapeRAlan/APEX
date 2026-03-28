import numpy as np
import rasterio
from pathlib import Path

class Preprocessor:
    def process_composite(self, tile_path: Path) -> np.ndarray:
        # Cargar las bandas y normalizar
        # Asumiendo 10 bandas + NDVI, GNDVI, NDWI, NBR
        with rasterio.open(tile_path) as src:
            # src.read() retorna [Bandas, Alto, Ancho]
            data = src.read()
            
        # Normalización simple [0, 1] asumiendo Sentinel-2 reflectancia superficial escalada * 10000
        # Las ultimas 4 bandas (indices x10000 o similar)
        
        normalized = data.astype(np.float32)
        # Bandas ópticas (primeras 10 bandas) aprox máximo 10000
        normalized[:10] = np.clip(normalized[:10] / 10000.0, 0, 1)
        
        # Índices: teóricamente entre -1 y 1
        # Depende de cómo GEE descarga las bandas extraídas.
        
        return normalized
