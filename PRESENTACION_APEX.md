# APEX — Plataforma Avanzada de Análisis Geoespacial Ambiental

## Para la Procuraduría Federal de Protección al Ambiente (PROFEPA)

---

## 1. ¿Qué es APEX?

APEX es una plataforma web de análisis geoespacial que integra **13 motores de detección**, modelos de inteligencia artificial y datos satelitales multi-fuente para el monitoreo ambiental del territorio mexicano. Combina imágenes Sentinel-1/2, Google Dynamic World, Hansen GFC, GLAD/RADD alerts, NASA FIRMS y modelos de deep learning (SpectralGPT+, ForestNet-MX, ConvLSTM) para generar análisis automatizados con respaldo científico.

**Objetivo principal:** Dotar a PROFEPA de una herramienta institucional para monitoreo ambiental, detección de ilícitos forestales y generación de reportes técnicos automatizados.

---

## 2. Problema que Resuelve

| Problema actual | Solución APEX |
|---|---|
| Inspecciones en campo costosas y lentas | Detección remota con 13 motores satelitales |
| Análisis manuales de imágenes | Pipeline automático multi-motor con cache inteligente |
| Sin registro histórico de cambios | Análisis temporal multi-año (2016–2025) configurable |
| Reportes manuales | PDF/Word institucional con secciones para cada motor |
| Dificultad para detectar anomalías | Z-score estadístico + AVOCADO NDVI + series temporales |
| Sin estimación de impacto ambiental | Biomasa GEDI + emisiones CO₂ por deforestación |
| Desconocimiento de causas | ForestNet-MX clasifica drivers de deforestación |

---

## 3. Arquitectura del Sistema

```
┌──────────────────────────────────────────────────────────────┐
│                   FRONTEND (React 19 + TypeScript 5.9)       │
│  MapLibre GL · Terra Draw · Recharts · Tailwind 4 · Vite 8  │
│  Puerto: 5173                                                │
└────────────────────────┬─────────────────────────────────────┘
                         │ HTTP/REST (48+ endpoints)
┌────────────────────────▼─────────────────────────────────────┐
│                   BACKEND (FastAPI 0.111 + Python 3.9)       │
│  Pipeline · GEE Service · Auth JWT · Report Generator        │
│  Puerto: 8003                                                │
├──────────────────────────────────────────────────────────────┤
│  MOTORES DE DETECCIÓN (13)                                   │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌──────────┐  │
│  │Deforestac. │ │ Vegetación │ │ Exp.Urbana │ │Estructur.│  │
│  │ (DW+NDVI)  │ │(DW 7-cls)  │ │  (DW)      │ │ (S2 HR)  │  │
│  └────────────┘ └────────────┘ └────────────┘ └──────────┘  │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌──────────┐  │
│  │Hansen GFC  │ │GLAD/RADD   │ │ Drivers    │ │ForestNet │  │
│  │(2000-2023) │ │ Alertas    │ │  (WRI)     │ │  -MX     │  │
│  └────────────┘ └────────────┘ └────────────┘ └──────────┘  │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌──────────┐  │
│  │SpectralGPT+│ │SAR Sentinel│ │FIRMS NRT   │ │ AVOCADO  │  │
│  │(ViT LULC)  │ │  -1 (VV/VH)│ │(VIIRS/MOD) │ │(anomalía)│  │
│  └────────────┘ └────────────┘ └────────────┘ └──────────┘  │
│  + Biomasa GEDI L4B · CO₂ estimator                         │
├──────────────────────────────────────────────────────────────┤
│  Google Earth Engine · PyTorch 2.3 (CUDA) · SQLite           │
│  Rasterio · GeoPandas · ReportLab · python-docx              │
└──────────────────────────────────────────────────────────────┘
```

---

## 4. Tecnologías Principales

### Backend
- **FastAPI 0.111** — Framework web asíncrono de alto rendimiento
- **Google Earth Engine** — Sentinel-2, Dynamic World, Hansen GFC, GLAD/RADD, GEDI
- **PyTorch 2.3 + CUDA 12.1** — SpectralGPT+ ViT, ForestNet-MX, ConvLSTM
- **Rasterio / GeoPandas / Shapely** — Procesamiento geoespacial
- **ReportLab / python-docx** — Reportes institucionales PDF y Word
- **SQLAlchemy 2.x + SQLite** — Persistencia con cache de resultados
- **NASA FIRMS API** — Puntos de calor VIIRS/MODIS en tiempo casi-real

### Frontend
- **React 19 + TypeScript 5.9** — 16 componentes de interfaz
- **MapLibre GL JS** — Mapas interactivos con capas vectoriales y raster
- **Terra Draw** — Dibujo y edición de polígonos AOI
- **Recharts** — Gráficas de series temporales
- **Tailwind CSS 4** — Diseño dark-mode responsivo
- **Vite 8** — Bundler con HMR

---

## 5. Motores de Análisis (13)

### Grupo 1: Detección Base (Dynamic World + Sentinel-2)

| Motor | Descripción | Salida |
|-------|-------------|--------|
| **Deforestación** | 6 índices espectrales (NDVI, BSI, SAVI, NBR, EVI, NDRE) + DW | Polígonos con área (ha), confianza, NDVI |
| **Vegetación** | 7 clases de cobertura (agua, bosque denso/ralo, pastizal, etc.) | Distribución % por clase |
| **Expansión Urbana** | Detección de cambio a zonas construidas vía DW | Polígonos de expansión con área |
| **Estructuras** | Detección de construcciones en resolución alta | Polígonos de edificaciones |

### Grupo 2: Pérdida Forestal Histórica

| Motor | Descripción | Salida |
|-------|-------------|--------|
| **Hansen GFC** | Datos Global Forest Change v1.11 (2000–2023) | Pérdida por año, tree cover, gain |
| **Alertas GLAD/RADD** | Alertas de deforestación en tiempo casi-real | Alertas con fechas y confianza |
| **Drivers (WRI)** | Causales de pérdida forestal (World Resources Institute) | Categorías: commodity, forestry, fire, urbanization |
| **ForestNet-MX** | Clasificación de drivers adaptada a México | 8 categorías (agricultura, ganadería, minería, etc.) |

### Grupo 3: IA y Sensores Avanzados

| Motor | Descripción | Salida |
|-------|-------------|--------|
| **SpectralGPT+** | ViT MAE pre-entrenado (768-dim, 12 bloques) + ensemble heurístico | 10 clases LULC con confianza |
| **SAR (Sentinel-1)** | Radar VV/VH para detección bajo nubes | Cambios estructurales |
| **AVOCADO** | Anomalías NDVI por Z-score temporal | Zonas con degradación anómala |

### Grupo 4: Incendios

| Motor | Descripción | Salida |
|-------|-------------|--------|
| **Incendios (MODIS)** | Índice NBR para áreas quemadas | Polígonos de quema con severidad |
| **FIRMS Hotspots** | Puntos de calor VIIRS/MODIS NRT | Clusters con FRP, confianza, satélites |

### Capacidades Adicionales
- **Biomasa GEDI L4B** — Estimación de biomasa aérea (Mg/ha) por polígono de deforestación
- **Emisiones CO₂** — Cálculo automático: AGB × 0.47 × 3.67 toneladas de CO₂
- **Validación cruzada** — Comparación multi-fuente entre motores para consistencia

---

## 6. Flujo de Trabajo

```
 1. DIBUJAR              2. SELECCIONAR          3. ANALIZAR
 ┌──────────────┐       ┌──────────────┐       ┌──────────────┐
 │  Polígono en │       │  Motores por │       │  Pipeline    │
 │  mapa o      │──────▶│  grupo con   │──────▶│  multi-motor │
 │  upload      │       │  Todos/Ning. │       │  con cache   │
 │  (SHP/KML/   │       │  + rango de  │       │  inteligente │
 │   GeoJSON)   │       │  años/season │       │              │
 └──────────────┘       └──────────────┘       └──────┬───────┘
                                                      │
 6. EXPORTAR            5. VALIDAR              4. VISUALIZAR
 ┌──────────────┐       ┌──────────────┐       ┌──────────────┐
 │  PDF/Word/   │       │  Fly-to por  │       │  13 capas de │
 │  JSON +      │◀──────│  detección,  │◀──────│  resultados  │
 │  email auto  │       │  aprobar o   │       │  + timeline  │
 │  con folio   │       │  rechazar    │       │  interactivo │
 └──────────────┘       └──────────────┘       └──────────────┘
```

---

## 7. Funcionalidades Clave

### Sistema de Cache Inteligente
- Resultados de motores se cachean por hash de AOI + fechas
- Timeline y análisis individual comparten resultados
- Reduce tiempos de re-análisis de minutos a segundos

### Selección de Motores por Grupos
- 4 categorías organizadas: Detección Base, Pérdida Forestal, IA/Sensores, Incendios
- Botones "Todos" / "Ninguno" para selección rápida
- Badge con conteo de motores activos por grupo

### Análisis Temporal Configurable
- Rango de años personalizable (2016–2025)
- 3 temporadas: Seca (Ene-Mar), Lluviosa (Jun-Sep), Anual
- Tendencias multi-anuales con detección automática de anomalías

### Generación de Reportes
- **PDF** con formato institucional PROFEPA (logotipos, branding)
- **Word (.docx)** editable para inspectores
- **JSON** para integración con otros sistemas
- Secciones automáticas para cada motor ejecutado (biomasa, CO₂, drivers, LULC, etc.)
- Envío por email con folio PROFEPA-APEX

### Paneles Especializados
- **Panel Estratégico** — Priorización de zonas de inspección
- **Simulador** — Proyección de cambios futuros (ConvLSTM)
- **Dashboard de Impacto** — Métricas de CO₂, biodiversidad, legal
- **Panel de Forecast** — Pronóstico de tendencias
- **Chat IA** — Consultas en lenguaje natural sobre resultados
- **Monitoreo** — Vigilancia automatizada de alertas

---

## 8. API REST (48+ endpoints)

### Análisis
| Método | Endpoint | Descripción |
|--------|----------|-------------|
| `POST` | `/api/analyze` | Análisis de fecha única (13 motores) |
| `POST` | `/api/timeline` | Análisis temporal multi-año configurable |
| `GET` | `/api/jobs/{id}` | Estado y progreso del trabajo |
| `GET` | `/api/results/{id}` | Resultados del análisis |
| `GET` | `/api/results/{id}/summary` | Resumen timeline con anomalías |

### Exportación
| Método | Endpoint | Descripción |
|--------|----------|-------------|
| `GET` | `/api/export/{id}/report` | Descarga PDF/Word/JSON |
| `POST` | `/api/results/{id}/send-report` | Enviar por email con folio |

### Autenticación
| Método | Endpoint | Descripción |
|--------|----------|-------------|
| `POST` | `/api/auth/login` | Login con JWT |
| `POST` | `/api/auth/register` | Registro de usuario |
| `GET` | `/api/auth/me` | Perfil del usuario |

### Validación
| Método | Endpoint | Descripción |
|--------|----------|-------------|
| `POST` | `/api/validate/feature` | Validar/rechazar detección |
| `GET` | `/api/validate/results/{id}` | Obtener validaciones |

### Datos y Tiles
| Método | Endpoint | Descripción |
|--------|----------|-------------|
| `GET` | `/api/tiles/{z}/{x}/{y}` | Tiles raster para mapa |
| `GET` | `/api/anp/check` | Verificar intersección con ANPs |
| `POST` | `/api/legal/context` | Contexto legal del AOI |

---

## 9. Base de Datos

**Motor:** SQLAlchemy 2.x + SQLite con migraciones automáticas

| Tabla | Propósito |
|-------|-----------|
| `jobs` | Trabajos: estado, progreso, AOI, motores, fechas, email |
| `analysis_results` | Resultados GeoJSON + stats por motor/año + cache hash |
| `gee_cache` | Cache de productos GEE descargados |
| `users` | Usuarios con roles (admin, inspector, viewer) |
| `validation_results` | Validaciones de detecciones por inspector |
| `monitoring_alerts` | Alertas de monitoreo automático |
| `monitoring_zones` | Zonas bajo vigilancia continua |
| `legal_opinions` | Contexto legal para reportes |
| `timeline_summaries` | Resúmenes de análisis temporal |
| `forecast_data` | Datos de predicción ConvLSTM |

---

## 10. Interfaz de Usuario (16 componentes)

| Componente | Función |
|------------|---------|
| **TopBar** | Selector de basemap, coordenadas, estado de conexión |
| **MapView** | Mapa interactivo con 10+ capas vectoriales y raster |
| **Sidebar** | Control: motores por grupo, AOI, estado, acciones |
| **TimelinePanel** | Gráficas de tendencias con Recharts |
| **ValidationPanel** | Inspección fly-to con aprobar/rechazar |
| **StatsCard** | Métricas colapsables por motor |
| **LegendPanel** | Leyenda de colores por detección |
| **MonitoringPanel** | Vigilancia automatizada de zonas |
| **ChatPanel** | Chat IA sobre resultados |
| **StrategicPanel** | Priorización de inspecciones |
| **SimulatorPanel** | Simulador de cambio ConvLSTM |
| **ImpactDashboard** | Dashboard CO₂ + biodiversidad |
| **ForecastPanel** | Pronóstico de tendencias |
| **LoginPage** | Autenticación JWT |
| **JobStatus** | Indicador de progreso en tiempo real |
| **PolygonManager** | Upload SHP/KML/GeoJSON + dibujo |

---

## 11. Fuentes de Datos

| Fuente | Resolución | Uso en APEX |
|--------|------------|-------------|
| **Sentinel-2** (ESA) | 10-30 m | 12 bandas multiespectrales para índices |
| **Sentinel-1** (ESA) | 10 m | SAR VV/VH para detección bajo nubes |
| **Dynamic World** (Google) | 10 m | Clasificación automática 9 clases |
| **Hansen GFC** (UMD) | 30 m | Pérdida forestal 2000–2023 |
| **GLAD/RADD** (UMD/WUR) | 10 m | Alertas de deforestación NRT |
| **GEDI L4B** (NASA/UMD) | 1 km | Biomasa aérea 2019–2023 |
| **NASA FIRMS** | 375m-1km | Puntos de calor VIIRS/MODIS NRT |
| **WRI Drivers** | 30 m | Causales de pérdida forestal |
| **CONAFOR/INEGI** | Variable | Datos para ForestNet-MX |
| **Google Earth Engine** | — | Plataforma de procesamiento y catálogos |

---

## 12. Modelos de IA

| Modelo | Arquitectura | Parámetros | Función |
|--------|-------------|------------|---------|
| **SpectralGPT+** | ViT MAE (Conv3d→12 Transformers→768d) | ~100M | Clasificación LULC 10 clases |
| **ForestNet-MX** | CNN adaptada a México | ~25M | Clasificación de drivers de deforestación |
| **ConvLSTM** | Conv. LSTM temporal | ~5M | Predicción de cambios futuros |
| **Ensemble heurístico** | Reglas NDVI/NDWI/NBR + ViT | — | Validación cruzada de SpectralGPT+ |

Modelo SpectralGPT+ almacenado en `data/ml_models/SpectralGPT+.pth` (1.1 GB).

---

## 13. Requisitos del Sistema

| Componente | Requisito |
|------------|-----------|
| **GPU** | NVIDIA con CUDA 12.1+ (RTX 4050 o superior) |
| **VRAM** | 5.5 GB mínimo para inferencia SpectralGPT+ |
| **Python** | 3.9+ con PyTorch 2.3, FastAPI, Earth Engine API |
| **Node.js** | 18+ para frontend React |
| **Credenciales** | Cuenta de servicio GEE + HuggingFace token (TEOChat) |
| **Almacenamiento** | ~10 GB para modelos + cache de tiles |

---

## 14. Ventajas Competitivas

1. **13 motores complementarios** en una sola plataforma integrada
2. **Cache inteligente** — resultados reutilizados entre análisis
3. **IA de vanguardia** — SpectralGPT+ ViT con ensemble para robustez
4. **Análisis temporal configurable** — 2016–2025, 3 temporadas
5. **Impacto ambiental cuantificado** — Biomasa GEDI + CO₂ por deforestación
6. **Causales de deforestación** — ForestNet-MX adaptado a México
7. **Multi-sensor** — Óptico (S2) + Radar (S1) + Térmico (FIRMS)
8. **Reportes institucionales** — PDF/Word con formato PROFEPA y envío por email
9. **Open source satelital** — Sentinel-1/2 gratuitos
10. **GPU acelerado** — Inferencia en tiempo real con CUDA

---

## 15. Casos de Uso

| Caso | Descripción | Motores principales |
|------|-------------|---------------------|
| **Verificación de denuncias** | Análisis puntual de zona denunciada | Deforestación, Hansen, Alertas |
| **Monitoreo periódico** | Timeline anual para detectar cambios | Todos (timeline configurable) |
| **Inspecciones previas** | Identificar prioridades antes de campo | Estratégico, Drivers, ForestNet-MX |
| **Expedientes técnicos** | Reporte con evidencia satelital | Todos + PDF/Word con folio |
| **Estimación de daño ambiental** | Cuantificar impacto ecológico | Biomasa GEDI, CO₂, AVOCADO |
| **Detección de incendios** | Puntos de calor y áreas quemadas | FIRMS, Incendios MODIS |
| **Vigilancia automatizada** | Monitoreo continuo de zonas críticas | Panel de Monitoreo + Alertas |

---

> **APEX** — Análisis y Protección del Entorno por Exploración satelital
>
> Desarrollado para la Procuraduría Federal de Protección al Ambiente (PROFEPA)
