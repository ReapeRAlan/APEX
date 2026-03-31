# APEX Frontend — Interfaz de Análisis Geoespacial

## Stack Tecnológico

- **React 19** + **TypeScript 5.9** — UI reactiva con tipado estricto
- **Vite 8** — Build tool con HMR
- **MapLibre GL JS** — Mapas interactivos con capas vectoriales/raster
- **Terra Draw** — Dibujo y edición de polígonos AOI
- **Recharts** — Gráficas de series temporales
- **Tailwind CSS 4** — Estilos dark-mode

## Componentes (16)

| Componente | Archivo | Función |
|------------|---------|---------|
| App | `App.tsx` | Root: estado global, handlers, layout |
| Sidebar | `Sidebar.tsx` | Motores por grupo, AOI, acciones, timeline |
| MapView | `MapView.tsx` | Mapa con 10+ capas + dibujo |
| TopBar | `TopBar.tsx` | Basemap selector, coords, estado |
| TimelinePanel | `TimelinePanel.tsx` | Gráficas temporales interactivas |
| ValidationPanel | `ValidationPanel.tsx` | Fly-to + aprobar/rechazar |
| StatsCard | `StatsCard.tsx` | Métricas colapsables por motor |
| LegendPanel | `LegendPanel.tsx` | Leyenda de colores |
| MonitoringPanel | `MonitoringPanel.tsx` | Vigilancia automatizada |
| ChatPanel | `ChatPanel.tsx` | Chat IA sobre resultados |
| StrategicPanel | `StrategicPanel.tsx` | Priorización de inspecciones |
| SimulatorPanel | `SimulatorPanel.tsx` | Simulador ConvLSTM |
| ImpactDashboard | `ImpactDashboard.tsx` | Dashboard CO₂ + biodiversidad |
| ForecastPanel | `ForecastPanel.tsx` | Pronóstico de tendencias |
| LoginPage | `LoginPage.tsx` | Autenticación JWT |
| PolygonManager | `PolygonManager.tsx` | Upload SHP/KML/GeoJSON |

## Desarrollo

```bash
npm install
npm run dev       # http://localhost:5173
```

## Build

```bash
npm run build     # Output: dist/
```

## Configuración

El backend se configura en `src/config.ts` — por defecto `http://localhost:8003`.
