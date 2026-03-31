# APEX — Análisis y Protección del Entorno por Exploración Satelital

> Plataforma de análisis geoespacial ambiental para la Procuraduría Federal de Protección al Ambiente (PROFEPA)

![Python](https://img.shields.io/badge/Python-3.9+-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.111-009688?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)
![TypeScript](https://img.shields.io/badge/TypeScript-5.9-3178C6?logo=typescript&logoColor=white)
![PyTorch](https://img.shields.io/badge/PyTorch-2.3-EE4C2C?logo=pytorch&logoColor=white)
![Earth Engine](https://img.shields.io/badge/Google%20Earth%20Engine-4285F4?logo=google-earth&logoColor=white)

---

## Tabla de Contenidos

- [¿Qué es APEX?](#qué-es-apex)
- [Arquitectura](#arquitectura)
- [Motores de Detección](#motores-de-detección-13)
- [Requisitos](#requisitos)
- [Instalación](#instalación)
- [Configuración](#configuración)
- [Inicio Rápido](#inicio-rápido)
- [API REST](#api-rest-48-endpoints)
- [Frontend](#frontend-16-componentes)
- [Base de Datos](#base-de-datos)
- [Pipeline de Análisis](#pipeline-de-análisis)
- [Fuentes de Datos](#fuentes-de-datos-satelitales)
- [Modelos de IA](#modelos-de-ia)
- [Despliegue](#despliegue)
- [Estructura del Proyecto](#estructura-del-proyecto)
- [Licencia](#licencia)

---

## ¿Qué es APEX?

APEX es una plataforma web que integra **13 motores de detección**, modelos de inteligencia artificial y datos satelitales multi-fuente para el monitoreo ambiental del territorio mexicano. Combina imágenes Sentinel-1/2, Google Dynamic World, Hansen GFC, GLAD/RADD, NASA FIRMS y modelos de deep learning (SpectralGPT+, ForestNet-MX, ConvLSTM) para generar análisis automatizados con respaldo científico.

### Problema que resuelve

| Problema actual | Solución APEX |
|---|---|
| Inspecciones en campo costosas y lentas | Detección remota con 13 motores satelitales |
| Análisis manuales de imágenes | Pipeline automático multi-motor con cache inteligente |
| Sin registro histórico de cambios | Análisis temporal multi-año (2016–2025) configurable |
| Reportes manuales | PDF/Word institucional automatizado con folio PROFEPA |
| Dificultad para detectar anomalías | Z-score estadístico + AVOCADO NDVI + series temporales |
| Sin estimación de impacto ambiental | Biomasa GEDI + emisiones CO₂ por deforestación |
| Desconocimiento de causas | ForestNet-MX clasifica drivers de deforestación |

---

## Arquitectura

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
│  Puerto: 8003/8008                                           │
├──────────────────────────────────────────────────────────────┤
│  MOTORES DE DETECCIÓN (13)                                   │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌──────────┐  │
│  │Deforestac. │ │ Vegetación │ │ Exp.Urbana │ │Estructur.│  │
│  │ (DW+NDVI)  │ │(DW 7-cls)  │ │  (DW)      │ │ (S2 HR)  │  │
│  └────────────┘ └────────────┘ └────────────┘ └──────────┘  │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌──────────┐  │
│  │Hansen GFC  │ │GLAD/RADD   │ │ Drivers    │ │ForestNet │  │
│  │(2000-2024) │ │ Alertas    │ │  (WRI)     │ │  -MX     │  │
│  └────────────┘ └────────────┘ └────────────┘ └──────────┘  │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌──────────┐  │
│  │SpectralGPT+│ │SAR Sentinel│ │FIRMS NRT   │ │ AVOCADO  │  │
│  │(ViT LULC)  │ │  -1 (VV/VH)│ │(VIIRS/MOD) │ │(anomalía)│  │
│  └────────────┘ └────────────┘ └────────────┘ └──────────┘  │
│  + Biomasa GEDI L4B · CO₂ estimator · Validación cruzada    │
├──────────────────────────────────────────────────────────────┤
│  Google Earth Engine · PyTorch 2.3 (CUDA) · SQLite           │
│  Rasterio · GeoPandas · ReportLab · python-docx              │
└──────────────────────────────────────────────────────────────┘
```

---

## Motores de Detección (13)

### Grupo 1 — Detección Base (Dynamic World + Sentinel-2)

| Motor | Descripción | Salida |
|-------|-------------|--------|
| **Deforestación** | 6 índices espectrales (NDVI, BSI, SAVI, NBR, EVI, NDRE) + DW | Polígonos con área (ha), confianza |
| **Vegetación** | 7 clases de cobertura via Dynamic World (10 m) | Distribución % por clase |
| **Expansión Urbana** | Detección de cambio T1→T2 a zonas construidas | Polígonos de expansión con área |
| **Estructuras** | Detección de construcciones en resolución alta | Polígonos de edificaciones |

### Grupo 2 — Pérdida Forestal Histórica

| Motor | Descripción | Salida |
|-------|-------------|--------|
| **Hansen GFC** | Datos Global Forest Change v1.11 (2000–2024) | Pérdida por año, tree cover, gain |
| **Alertas GLAD/RADD** | Alertas de deforestación en tiempo casi-real | Alertas con fechas y confianza |
| **Drivers (WRI)** | Causales de pérdida forestal (World Resources Institute) | Categorías: commodity, forestry, fire, urbanization |
| **ForestNet-MX** | Clasificación de drivers adaptada a México | 8 categorías (agricultura, ganadería, minería, etc.) |

### Grupo 3 — IA y Sensores Avanzados

| Motor | Descripción | Salida |
|-------|-------------|--------|
| **SpectralGPT+** | Vision Transformer MAE (768-dim, 12 bloques) + ensemble | 10 clases LULC con confianza |
| **SAR (Sentinel-1)** | Log-ratio change detection VV/VH bajo nubes | Cambios estructurales en dB |
| **AVOCADO** | Anomalías NDVI por percentil contra baseline multi-año | Zonas con degradación anómala por severidad |

### Grupo 4 — Incendios

| Motor | Descripción | Salida |
|-------|-------------|--------|
| **Incendios (MODIS)** | MCD64A1 áreas quemadas + correlación con deforestación | Polígonos de quema con severidad |
| **FIRMS Hotspots** | Puntos de calor VIIRS/MODIS NRT | Clusters con FRP, confianza, satélites |

### Post-procesamiento

| Módulo | Función |
|--------|---------|
| **Biomasa GEDI L4B** | Estimación de biomasa aérea (Mg/ha) por polígono de deforestación |
| **Emisiones CO₂** | Cálculo automático: AGB × 0.47 × 3.67 toneladas de CO₂ |
| **Validación cruzada** | Comparación DW vs MapBiomas para consistencia multi-fuente |
| **Contexto legal** | Verificación de intersección con Áreas Naturales Protegidas (ANPs) |

---

## Requisitos

| Componente | Mínimo |
|------------|--------|
| **OS** | Windows 10/11, Linux (Ubuntu 20.04+) |
| **Python** | 3.9+ |
| **Node.js** | 18+ |
| **GPU** | NVIDIA con CUDA 12.1+ (RTX 4050 o superior recomendada) |
| **VRAM** | 5.5 GB mínimo (inferencia SpectralGPT+) |
| **RAM** | 16 GB mínimo |
| **Almacenamiento** | ~10 GB para modelos + cache de tiles |
| **Credenciales** | Cuenta de servicio Google Earth Engine |

---

## Instalación

### 1. Clonar repositorio

```bash
git clone https://github.com/ReapeRAlan/APEX.git
cd APEX
```

### 2. Backend (Python)

```bash
# Crear entorno virtual
python -m venv venv

# Activar (Windows)
venv\Scripts\activate
# Activar (Linux/Mac)
source venv/bin/activate

# Instalar dependencias
pip install -r backend/requirements.txt
```

### 3. Frontend (Node.js)

```bash
cd frontend
npm install
cd ..
```

### 4. Base de datos

La base de datos SQLite se crea automáticamente al iniciar el backend. No requiere configuración adicional.

### 5. Google Earth Engine

```bash
# Autenticación interactiva
earthengine authenticate

# O usar cuenta de servicio (producción):
# Colocar gee_key.json en el directorio raíz
```

---

## Configuración

Crear archivo `.env` en el directorio raíz:

```env
# ── Servidor ──
BACKEND_PORT=8003
DATA_DIR=./data/tiles
DB_PATH=./db/apex.sqlite

# ── Google Earth Engine ──
GEE_AUTH_MODE=interactive
# Para producción con cuenta de servicio:
# GEE_AUTH_MODE=service_account
# GEE_SERVICE_ACCOUNT_EMAIL=your-sa@project.iam.gserviceaccount.com
# GEE_KEY_FILE=./gee_key.json

# ── GPU / Inferencia ──
CUDA_VISIBLE_DEVICES=0
TORCH_DEVICE=cuda
MAX_VRAM_GB=5.5
INFERENCE_BATCH_SIZE=4

# ── NASA FIRMS (puntos de calor NRT) ──
FIRMS_MAP_KEY=your_firms_api_key
FIRMS_SOURCES=VIIRS_SNPP_NRT,VIIRS_NOAA20_NRT,VIIRS_NOAA21_NRT

# ── Email (alertas y reportes) ──
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your_email@gmail.com
SMTP_PASS=your_app_password
ALERT_FROM_EMAIL=apex@profepa.gob.mx

# ── Autenticación JWT ──
SECRET_KEY=your_secret_key_here
JWT_ALGORITHM=HS256
JWT_EXPIRE_HOURS=8
```

> **Nota:** La clave FIRMS se obtiene en [https://firms.modaps.eosdis.nasa.gov/api/area/](https://firms.modaps.eosdis.nasa.gov/api/area/)

---

## Inicio Rápido

### Desarrollo (Windows)

```batch
@rem Backend (puerto 8003)
start "APEX-Backend" cmd /k "venv\Scripts\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8003 --reload"

@rem Frontend (puerto 5173)
start "APEX-Frontend" cmd /k "cd frontend && npm run dev"
```

O usar el script incluido:

```batch
start_apex.bat
```

### Desarrollo (Linux/Mac)

```bash
# Backend
source venv/bin/activate
uvicorn backend.main:app --host 0.0.0.0 --port 8003 --reload &

# Frontend
cd frontend && npm run dev &
```

### Acceso

| Servicio | URL |
|----------|-----|
| Frontend | [http://localhost:5173](http://localhost:5173) |
| API Docs (Swagger) | [http://localhost:8003/docs](http://localhost:8003/docs) |
| API Docs (ReDoc) | [http://localhost:8003/redoc](http://localhost:8003/redoc) |
| Health Check | [http://localhost:8003/health](http://localhost:8003/health) |

---

## API REST (48+ endpoints)

### Análisis

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| `POST` | `/api/analyze` | Análisis unificado (13 motores) |
| `POST` | `/api/timeline` | Análisis temporal multi-año configurable |
| `GET` | `/api/jobs/{id}` | Estado y progreso del trabajo |
| `GET` | `/api/results/{id}` | Resultados del análisis con GeoJSON + stats |
| `GET` | `/api/results/{id}/summary` | Resumen timeline con anomalías |

### Exportación

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| `GET` | `/api/export/{id}/report` | Descarga reporte PDF/Word/JSON |
| `POST` | `/api/results/{id}/send-report` | Enviar por email con folio PROFEPA |

### Chat IA

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| `POST` | `/api/chat/query` | Consulta en lenguaje natural sobre resultados |
| `GET` | `/api/chat/status` | Estado del servicio de IA |

### Predicción / Forecast

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| `POST` | `/api/forecast/run` | Ejecutar predicción temporal (trend/ml/ensemble) |
| `POST` | `/api/forecast/train-convlstm` | Entrenar modelo ConvLSTM |
| `GET` | `/api/forecast/status` | Estado del modelo |

### Autenticación

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| `POST` | `/api/auth/login` | Login con JWT |
| `POST` | `/api/auth/register` | Registro de usuario |
| `GET` | `/api/auth/me` | Perfil del usuario autenticado |

### Monitoreo

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| `POST` | `/api/monitoring` | Crear zona de monitoreo |
| `GET` | `/api/monitoring/history` | Historial de alertas |
| `DELETE`| `/api/monitoring/{id}` | Eliminar zona de monitoreo |

### Polígonos

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| `POST` | `/api/polygons/upload` | Upload GeoJSON/KML/Shapefile |
| `POST` | `/api/polygons/parse-wkt` | Parsear WKT a GeoJSON |

### Grid H3 / Estratégico / KPIs

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| `GET` | `/api/grid` | Grid hexagonal H3 |
| `GET` | `/api/strategic/overview` | Vista estratégica general |
| `GET` | `/api/kpi/summary` | Métricas KPI del sistema |

---

## Frontend (16 componentes)

| Componente | Función |
|------------|---------|
| **TopBar** | Selector de basemap (5 opciones), coordenadas del cursor, estado de conexión |
| **MapView** | Mapa interactivo MapLibre GL con 10+ capas vectoriales/raster y dibujo AOI |
| **Sidebar** | Selección de motores por grupo, configuración de análisis, tabs de resultados |
| **StatsCard** | Tarjetas métricas colapsables por cada motor con gráficas |
| **TimelinePanel** | Gráficas de tendencias multi-año con Recharts |
| **ForecastPanel** | Pronóstico temporal con polling de timeline y predicción ensemble |
| **ValidationPanel** | Interfaz de inspección fly-to: aprobar/rechazar detecciones |
| **ChatPanel** | Chat IA local sobre resultados del análisis (8 preguntas sugeridas) |
| **MonitoringPanel** | Configuración de vigilancia automatizada de zonas |
| **StrategicPanel** | Priorización de zonas para inspección |
| **SimulatorPanel** | Simulador de escenarios futuros (ConvLSTM) |
| **ImpactDashboard** | Dashboard de CO₂, biodiversidad y métricas de impacto |
| **LegendPanel** | Leyenda de colores por tipo de detección |
| **LoginPage** | Autenticación JWT con email/contraseña |
| **JobStatus** | Indicador de progreso en tiempo real con logs del servidor |
| **PolygonManager** | Upload y gestión de SHP/KML/GeoJSON + dibujo en mapa |

### Stack Frontend

- **React 19** + **TypeScript 5.9**
- **Vite 8** con HMR
- **MapLibre GL JS 5** + **Terra Draw** (dibujo de polígonos)
- **Recharts** (gráficas de series temporales)
- **Tailwind CSS 4** (diseño dark-mode responsivo)

---

## Base de Datos

**Motor:** SQLite con SQLAlchemy 2.x (migraciones automáticas al iniciar)

| Tabla | Propósito |
|-------|-----------|
| `jobs` | Trabajos: estado, progreso, AOI, motores, fechas, email, logs |
| `analysis_results` | Resultados GeoJSON + stats JSON por motor/año |
| `gee_cache` | Cache de productos GEE descargados (por hash AOI+fechas) |
| `users` | Usuarios con roles (admin, inspector, viewer) |
| `validation_results` | Validaciones de detecciones por inspector |
| `monitoring_areas` | Zonas bajo vigilancia continua |
| `monitoring_alerts` | Historial de alertas de monitoreo |
| `belief_states` | Estados bayesianos por celda H3 |
| `forecast_data` | Datos de predicción ConvLSTM |

---

## Pipeline de Análisis

### Análisis Principal (`run_pipeline`)

```
1. Validación de AOI y cache
2. Descarga S2 composite (tiles @ 10m)
3. Descarga Dynamic World T1/T2 (tiles @ 10m)
4. Ejecución paralela de 13 motores:
   ├── Deforestación (6 índices espectrales)
   ├── Vegetación (DW 7 clases)
   ├── Expansión Urbana (DW T1→T2)
   ├── Estructuras (S2 HR)
   ├── SpectralGPT+ (ViT MAE 10 clases)
   ├── Hansen GFC (2000-2024)
   ├── Alertas GLAD/RADD (NRT)
   ├── Drivers WRI
   ├── Incendios MODIS (MCD64A1)
   ├── SAR Sentinel-1 (log-ratio VV/VH)
   ├── FIRMS Hotspots (VIIRS/MODIS NRT)
   └── AVOCADO (NDVI anomalías percentil)
5. Post-procesamiento:
   ├── Biomasa GEDI L4B + CO₂
   ├── ForestNet-MX (drivers México)
   ├── Validación cruzada DW vs MapBiomas
   └── Contexto legal (ANPs)
6. Guardar resultados en DB
7. Notificación por email (opcional)
```

### Timeline (`run_timeline_pipeline`)

```
1. Loop por cada año (ej. 2018→2025)
2. Por año: ejecutar 13 motores con composite anual
3. Calcular métricas acumulativas por año
4. Detección de anomalías con Z-score
5. Generar resumen temporal (tendencias + anomalías)
```

---

## Fuentes de Datos Satelitales

| Fuente | Resolución | Uso en APEX |
|--------|------------|-------------|
| **Sentinel-2** (ESA) | 10–30 m | 12 bandas multiespectrales para índices (NDVI, NBR, EVI, etc.) |
| **Sentinel-1** (ESA) | 10 m | SAR VV/VH para detección bajo nubes |
| **Dynamic World** (Google) | 10 m | Clasificación automática 9 clases (NRT) |
| **Hansen GFC** (UMD) | 30 m | Pérdida forestal acumulada 2000–2024 |
| **GLAD** (UMD) | 10 m | Alertas de deforestación cuasi-NRT (Landsat) |
| **RADD** (WUR) | 10 m | Alertas de deforestación por radar (Sentinel-1) |
| **GEDI L4B** (NASA/UMD) | 1 km | Biomasa aérea (Mg/ha) 2019–2023 |
| **NASA FIRMS** | 375 m–1 km | Puntos de calor VIIRS/MODIS en tiempo casi-real |
| **MODIS MCD64A1** | 500 m | Áreas quemadas mensuales |
| **WRI Drivers** | 30 m | Causales de pérdida forestal global |
| **MapBiomas** | 30 m | Validación cruzada de cobertura de suelo |

Todos accedidos vía **Google Earth Engine** (high-volume endpoint).

---

## Modelos de IA

| Modelo | Arquitectura | Parámetros | Función |
|--------|-------------|------------|---------|
| **SpectralGPT+** | ViT MAE (Conv3d → 12 Transformers → 768d) | ~100M | Clasificación LULC 10 clases |
| **ForestNet-MX** | CNN adaptada a México | ~25M | Clasificación de drivers de deforestación |
| **ConvLSTM** | Convolutional LSTM temporal | ~5M | Predicción de cambios futuros |
| **Ensemble heurístico** | Reglas NDVI/NDWI/NBR + ViT | — | Validación cruzada de SpectralGPT+ |
| **IA Local (chat)** | Rule-based NLP | — | Consultas en lenguaje natural sobre resultados |

SpectralGPT+ se almacena en `data/ml_models/SpectralGPT+.pth` (~1.1 GB). Requiere GPU con CUDA.

---

## Despliegue

### Servicio Windows (producción)

```batch
@rem Requiere NSSM (Non-Sucking Service Manager) + permisos de administrador
install_service.bat
```

Configura auto-restart cada 5s en caso de fallo. Logs en `logs/`.

### Docker

```bash
# Desarrollo
docker-compose up --build

# Producción
docker-compose -f docker-compose.yml -f docker-compose.override.yml up -d
```

### Puertos por defecto

| Servicio | Puerto |
|----------|--------|
| Backend API | 8003 (servicio) / 8008 (dev) |
| Frontend | 5173 (dev) |
| API Docs | `{backend_port}/docs` |

---

## Estructura del Proyecto

```
APEX/
├── README.md                    # Este archivo
├── PRESENTACION_APEX.md         # Presentación técnica completa
├── start_apex.bat               # Script de inicio Windows
├── install_service.bat          # Instalación como servicio Windows
│
├── backend/
│   ├── main.py                  # Entry point FastAPI + CORS + routers
│   ├── config.py                # Configuración (.env + defaults)
│   ├── pipeline.py              # Orquestador principal (13 motores)
│   ├── requirements.txt         # Dependencias Python
│   │
│   ├── routers/                 # 12 routers — 48+ endpoints
│   │   ├── analysis.py          # /api/analyze, /api/timeline, /api/results
│   │   ├── chat.py              # /api/chat/query
│   │   ├── export.py            # /api/export/{id}/report
│   │   ├── forecast.py          # /api/forecast/run
│   │   ├── auth.py              # /api/auth/login, register
│   │   ├── monitoring.py        # /api/monitoring CRUD
│   │   ├── polygons.py          # /api/polygons/upload
│   │   ├── grid.py              # /api/grid (H3)
│   │   ├── beliefs.py           # /api/beliefs (bayesiano)
│   │   ├── pomdp.py             # /api/pomdp (planificación)
│   │   ├── strategic.py         # /api/strategic
│   │   └── kpi.py               # /api/kpi
│   │
│   ├── engines/                 # 13 motores de detección
│   │   ├── deforestation_engine.py
│   │   ├── vegetation_engine.py
│   │   ├── dynamic_world_engine.py
│   │   ├── structure_engine.py
│   │   ├── hansen_engine.py
│   │   ├── alerts_engine.py
│   │   ├── drivers_engine.py
│   │   ├── drivers_mx_engine.py      # ForestNet-MX
│   │   ├── spectralgpt_engine.py     # SpectralGPT+ ViT
│   │   ├── sar_engine.py             # Sentinel-1 SAR
│   │   ├── fire_engine.py            # MODIS MCD64A1
│   │   ├── firms_engine.py           # NASA FIRMS NRT
│   │   ├── avocado_engine.py         # NDVI anomalías
│   │   ├── biomass_engine.py         # GEDI L4B
│   │   ├── crossval_engine.py        # Validación cruzada
│   │   └── legal_engine.py           # Contexto ANPs
│   │
│   ├── services/                # 30+ servicios
│   │   ├── gee_service.py       # GEE core: S2 composites, tiles
│   │   ├── gee_hansen.py        # Hansen GFC data
│   │   ├── gee_alerts.py        # GLAD/RADD/MODIS downloads
│   │   ├── gee_drivers.py       # WRI drivers dataset
│   │   ├── gee_sar.py           # Sentinel-1 composites
│   │   ├── gee_biomass.py       # GEDI L4B extraction
│   │   ├── gee_avocado.py       # NDVI anomaly detection
│   │   ├── gee_legal.py         # ANP intersections
│   │   ├── firms_service.py     # NASA FIRMS API
│   │   ├── spectral_indices.py  # NDVI, SAVI, NBR, EVI, etc.
│   │   ├── forecast_engine.py   # Predicción temporal
│   │   ├── convlstm_model.py    # ConvLSTM training/inference
│   │   ├── local_chat_service.py    # Chat IA local
│   │   ├── report_generator.py  # PDF/Word/JSON reports
│   │   ├── auth_service.py      # JWT + bcrypt
│   │   ├── alert_service.py     # Email dispatch (SMTP)
│   │   ├── bayesian_fusion.py   # Belief state fusion
│   │   └── pomdp_optimizer.py   # Inspection planning
│   │
│   └── db/                      # SQLite + SQLAlchemy
│       ├── models.py            # ORM models (12 tablas)
│       └── session.py           # Engine + session factory
│
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── index.html
│   └── src/
│       ├── App.tsx              # Root: estado global, análisis unificado
│       ├── config.ts            # API_BASE_URL
│       └── components/          # 16 componentes React
│           ├── MapView.tsx
│           ├── Sidebar.tsx
│           ├── StatsCard.tsx
│           ├── TopBar.tsx
│           ├── TimelinePanel.tsx
│           ├── ForecastPanel.tsx
│           ├── ChatPanel.tsx
│           ├── ValidationPanel.tsx
│           ├── MonitoringPanel.tsx
│           ├── StrategicPanel.tsx
│           ├── SimulatorPanel.tsx
│           ├── ImpactDashboard.tsx
│           ├── LegendPanel.tsx
│           ├── LoginPage.tsx
│           ├── JobStatus.tsx
│           └── PolygonManager.tsx
│
├── data/
│   ├── tiles/                   # Cache de rasters GEE
│   └── ml_models/               # Pesos de modelos (SpectralGPT+.pth)
│
├── db/                          # SQLite database files
└── logs/                        # Application logs
```

---

## Dependencias Principales

### Python (Backend)

| Categoría | Paquetes |
|-----------|----------|
| **API** | FastAPI 0.111, uvicorn 0.30, python-multipart |
| **Geoespacial** | rasterio 1.3, geopandas 0.14, shapely 2.0, pyproj 3.6, h3 3.7 |
| **Earth Engine** | earthengine-api 0.1.400 |
| **Deep Learning** | torch 2.3+cu121, torchvision 0.18, segmentation-models-pytorch |
| **Procesamiento** | numpy 1.26, scipy 1.13, scikit-learn 1.4, opencv-python-headless |
| **Reportes** | reportlab, python-docx, matplotlib, Pillow |
| **Base de datos** | SQLAlchemy 2.0 |
| **Auth** | python-jose (JWT), bcrypt, passlib |

### Node.js (Frontend)

| Paquete | Versión |
|---------|---------|
| react | 19.x |
| maplibre-gl | 5.x |
| terra-draw | latest |
| recharts | 2.x |
| tailwindcss | 4.x |
| vite | 8.x |
| typescript | 5.9 |

---

## Flujo de Trabajo del Usuario

```
 1. DIBUJAR              2. CONFIGURAR           3. ANALIZAR
 ┌──────────────┐       ┌──────────────┐       ┌──────────────┐
 │  Polígono en │       │  Motores por │       │  Pipeline    │
 │  mapa o subir│──────▶│  grupo + año │──────▶│  multi-motor │
 │  SHP/KML/    │       │  + temporada │       │  con progreso│
 │  GeoJSON     │       │  (seca/lluv) │       │  en tiempo   │
 └──────────────┘       └──────────────┘       │  real        │
                                               └──────┬───────┘
                                                      │
 6. EXPORTAR            5. VALIDAR              4. VISUALIZAR
 ┌──────────────┐       ┌──────────────┐       ┌──────────────┐
 │  PDF/Word    │       │  Fly-to por  │       │  13 capas en │
 │  reporte con │◀──────│  detección,  │◀──────│  mapa + stats│
 │  folio y     │       │  aprobar o   │       │  + timeline  │
 │  email auto  │       │  rechazar    │       │  + Chat IA   │
 └──────────────┘       └──────────────┘       └──────────────┘
```

---

## Casos de Uso

| Caso | Descripción | Motores principales |
|------|-------------|---------------------|
| **Verificación de denuncias** | Análisis puntual de zona denunciada | Deforestación, Hansen, Alertas |
| **Monitoreo periódico** | Timeline anual para detectar cambios | Todos (timeline configurable) |
| **Inspecciones previas** | Identificar prioridades antes de campo | Estratégico, Drivers, ForestNet-MX |
| **Expedientes técnicos** | Reporte con evidencia satelital | Todos + PDF/Word con folio |
| **Estimación de daño ambiental** | Cuantificar impacto ecológico | Biomasa GEDI, CO₂, AVOCADO |
| **Detección de incendios** | Puntos de calor y áreas quemadas | FIRMS, Incendios MODIS |
| **Vigilancia automatizada** | Monitoreo continuo de zonas críticas | Panel de Monitoreo + Alertas email |

---

## Licencia

Desarrollado para la **Procuraduría Federal de Protección al Ambiente (PROFEPA)**.

Uso institucional. Todos los derechos reservados.

---

> **APEX** — Análisis y Protección del Entorno por Exploración Satelital
