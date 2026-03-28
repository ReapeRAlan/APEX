# APEX — Plataforma Avanzada de Análisis Geoespacial Ambiental

## Para la Procuraduría Federal de Protección al Ambiente (PROFEPA)

---

## 1. ¿Qué es APEX?

APEX es una plataforma web de análisis geoespacial diseñada para la **detección de deforestación, expansión urbana y clasificación de cobertura vegetal** en el territorio mexicano. Combina imágenes satelitales multiespectrales (Sentinel-2), clasificación de Google Dynamic World y aprendizaje profundo (Deep Learning) para generar análisis automatizados de cambio de uso de suelo.

**Objetivo principal:** Dotar a PROFEPA de una herramienta institucional para monitoreo ambiental, detección de ilícitos forestales y generación de reportes técnicos con respaldo científico.

---

## 2. Problema que Resuelve

| Problema actual | Solución APEX |
|---|---|
| Inspecciones en campo costosas y lentas | Detección remota automatizada con imágenes satelitales |
| Análisis manuales de imágenes satelitales | Motores de análisis automáticos con índices espectrales |
| Falta de registro histórico de cambios | Análisis temporal multi-año (2018–2025) |
| Reportes técnicos elaborados manualmente | Generación automática de reportes PDF/Word con formato institucional PROFEPA |
| Dificultad para detectar anomalías | Detección estadística de anomalías (Z-score) en series temporales |

---

## 3. Arquitectura del Sistema

```
┌─────────────────────────────────────────────────────────┐
│                    FRONTEND (React + TypeScript)         │
│   MapLibre GL · Terra Draw · Recharts · Tailwind CSS    │
│   Puerto: 5173                                          │
└──────────────────────┬──────────────────────────────────┘
                       │ HTTP/REST
┌──────────────────────▼──────────────────────────────────┐
│                    BACKEND (FastAPI + Python)            │
│   Motores de Análisis · Pipeline · GEE Service          │
│   Puerto: 8000-8007                                     │
├─────────────────────────────────────────────────────────┤
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────┐  │
│  │Deforest. │ │ Dynamic  │ │Vegetación│ │Estructura │  │
│  │ Engine   │ │  World   │ │  Engine  │ │  Engine   │  │
│  └──────────┘ └──────────┘ └──────────┘ └───────────┘  │
├─────────────────────────────────────────────────────────┤
│  Google Earth Engine API · PyTorch (CUDA/GPU)           │
│  SQLite · Rasterio · GeoPandas                          │
└─────────────────────────────────────────────────────────┘
```

---

## 4. Tecnologías Principales

### Backend
- **FastAPI** — Framework web de alto rendimiento
- **Google Earth Engine** — Acceso a imágenes Sentinel-2 y Dynamic World
- **PyTorch + CUDA** — Inferencia con GPU (RTX 4050, 5.5 GB VRAM)
- **Rasterio / GeoPandas / Shapely** — Procesamiento geoespacial
- **ReportLab / python-docx** — Generación de reportes institucionales
- **SQLite** — Base de datos ligera para trabajos y resultados

### Frontend
- **React 19 + TypeScript** — Interfaz de usuario moderna
- **MapLibre GL** — Visualización de mapas interactivos
- **Terra Draw** — Dibujo de polígonos de área de interés
- **Recharts** — Gráficas de análisis temporal
- **Tailwind CSS** — Diseño responsivo

---

## 5. Motores de Análisis

### 5.1 Motor de Deforestación
Detecta pérdida de cobertura forestal mediante 6 índices espectrales:

| Índice | Función |
|--------|---------|
| **NDVI** | Vigor de vegetación |
| **BSI** | Índice de suelo desnudo |
| **SAVI** | Vegetación ajustada al suelo |
| **NBR** | Ratio de quema normalizado |
| **EVI** | Índice de vegetación mejorado |
| **NDRE** | Borde rojo de vegetación |

**Salida:** Polígonos GeoJSON con área (ha), nivel de confianza y métricas NDVI.

### 5.2 Motor Dynamic World
Utiliza la clasificación de Google Dynamic World V1 con **9 clases de cobertura**:

- Agua · Bosque denso · Pastizal · Vegetación inundable
- Cultivos · Matorral · Zona urbana · Suelo desnudo · Nieve/Hielo

Permite **detección de cambios** entre periodos temporales con visualización por colores.

### 5.3 Motor de Vegetación
Clasificación en **7 tipos de cobertura** basada en índices espectrales:

- Agua · Bosque denso · Bosque ralo · Pastizal · Suelo · Urbano · Quemado

**Salida:** Distribución porcentual por clase de cobertura.

### 5.4 Motor de Estructuras
Diseñado para detección de construcciones (actualmente deshabilitado — requiere imágenes de resolución <1 m; Sentinel-2 opera a 10-30 m).

---

## 6. Flujo de Trabajo del Usuario

```
 1. DIBUJAR              2. CONFIGURAR           3. ANALIZAR
 ┌──────────────┐       ┌──────────────┐       ┌──────────────┐
 │  El usuario  │       │  Selecciona  │       │  APEX lanza  │
 │  dibuja un   │──────▶│  motores de  │──────▶│  el análisis │
 │  polígono    │       │  análisis y  │       │  en segundo  │
 │  sobre el    │       │  rango de    │       │  plano       │
 │  mapa        │       │  fechas      │       │              │
 └──────────────┘       └──────────────┘       └──────┬───────┘
                                                      │
 6. EXPORTAR            5. VALIDAR              4. VISUALIZAR
 ┌──────────────┐       ┌──────────────┐       ┌──────────────┐
 │  Genera      │       │  Inspección  │       │  Resultados  │
 │  reporte     │◀──────│  interactiva │◀──────│  en mapa con │
 │  PDF / Word  │       │  de cada     │       │  estadísticas│
 │  con formato │       │  detección   │       │  y capas     │
 │  PROFEPA     │       │              │       │  de colores  │
 └──────────────┘       └──────────────┘       └──────────────┘
```

---

## 7. Funcionalidades Clave

### Análisis de Fecha Única
- Ejecución multi-motor para un periodo específico
- Ideal para inspecciones puntuales y verificación de denuncias

### Análisis Temporal (Timeline)
- Tendencias multi-anuales de **2018 a 2025**
- Comparación año por año de cobertura vegetal
- Soporte para estaciones: seca, lluviosa y anual
- **Detección de anomalías** con Z-score estadístico

### Manejo de Áreas Grandes
- AOIs mayores a **150 km²** se dividen automáticamente en cuadrículas (hasta 5×5)
- Procesamiento paralelo por segmentos

### Reportes Institucionales
- **PDF** con branding PROFEPA (logotipos, formato institucional)
- **Word (.docx)** para edición posterior
- **JSON** para integración con otros sistemas
- Contenido: resumen ejecutivo, tablas, gráficas, mapas, alertas de anomalías, anexo metodológico

---

## 8. API REST

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| `POST` | `/api/analyze` | Lanza análisis de fecha única |
| `POST` | `/api/timeline` | Lanza análisis temporal multi-año |
| `GET` | `/api/jobs/{id}` | Consulta estado y progreso del trabajo |
| `GET` | `/api/results/{id}` | Obtiene resultados del análisis |
| `GET` | `/api/export/{id}/report` | Descarga reporte (PDF, Word, JSON) |

---

## 9. Base de Datos

**Motor:** SQLite (ligero, sin servidor)

| Tabla | Propósito |
|-------|-----------|
| `jobs` | Registro de trabajos: estado, progreso, AOI, motores, fechas |
| `analysis_results` | Resultados GeoJSON y estadísticas por motor/año |
| `gee_cache` | Caché de imágenes descargadas de Google Earth Engine |

---

## 10. Interfaz de Usuario

### Componentes principales:

| Componente | Función |
|------------|---------|
| **TopBar** | Selector de mapa base, coordenadas, indicador de estado |
| **MapView** | Mapa interactivo con capas de resultados y dibujo de polígonos |
| **Sidebar** | Panel de control: motores, fechas, estado del trabajo, estadísticas |
| **TimelinePanel** | Gráficas de tendencias temporales con Recharts |
| **ValidationPanel** | Inspección interactiva de detecciones (fly-to) |
| **StatsCard** | Tarjetas colapsables con métricas por motor |
| **LegendPanel** | Leyenda de colores para cada tipo de detección |

---

## 11. Fuentes de Datos

| Fuente | Resolución | Uso en APEX |
|--------|------------|-------------|
| **Sentinel-2** (ESA) | 10-30 m | Bandas multiespectrales para índices de vegetación |
| **Dynamic World** (Google) | 10 m | Clasificación automática de cobertura terrestre |
| **Google Earth Engine** | — | Plataforma de procesamiento y acceso a catálogos |

---

## 12. Requisitos del Sistema

| Componente | Requisito |
|------------|-----------|
| **GPU** | NVIDIA con CUDA (RTX 4050 o superior recomendada) |
| **VRAM** | Mínimo 5.5 GB para inferencia |
| **Python** | 3.10+ con PyTorch, FastAPI, Earth Engine API |
| **Node.js** | 18+ para frontend React |
| **Credenciales** | Cuenta de servicio de Google Earth Engine |
| **Almacenamiento** | Espacio para caché de tiles e imágenes satelitales |

---

## 13. Ventajas Competitivas

1. **Especializado para PROFEPA** — Reportes con formato institucional, terminología oficial
2. **Multi-motor** — 4 motores de análisis complementarios en una sola plataforma
3. **Análisis temporal** — Tendencias de 7+ años con detección automática de anomalías
4. **GPU acelerado** — Inferencia con Deep Learning en tiempo real
5. **Open source satelital** — Usa Sentinel-2 (gratuito, sin costo por imagen)
6. **Exportación institucional** — PDF y Word listos para expedientes oficiales
7. **Escalable** — Manejo automático de áreas grandes mediante cuadrículas

---

## 14. Casos de Uso

| Caso | Descripción |
|------|-------------|
| **Verificación de denuncias** | Análisis puntual para confirmar deforestación reportada |
| **Monitoreo periódico** | Timeline anual para detectar cambios graduales |
| **Inspecciones previas** | Identificar zonas prioritarias antes de visitas en campo |
| **Expedientes técnicos** | Generación de reportes con evidencia satelital |
| **Detección de cambio de uso de suelo** | Identificar conversión ilegal de bosque a uso agrícola o urbano |

---

## 15. Roadmap Futuro

- Integración de imágenes de alta resolución (<1 m) para activar el motor de estructuras
- Autenticación de usuarios y control de acceso basado en roles
- Alertas automáticas por correo al detectar anomalías
- Dashboard de monitoreo continuo con análisis programados
- Integración con sistemas institucionales de PROFEPA

---

> **APEX** — Análisis y Protección del Entorno por Exploración satelital
>
> Desarrollado para la Procuraduría Federal de Protección al Ambiente (PROFEPA)
