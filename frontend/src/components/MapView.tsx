import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react"
import maplibregl from "maplibre-gl"
import "maplibre-gl/dist/maplibre-gl.css"
import { TerraDraw, TerraDrawPolygonMode, TerraDrawSelectMode, TerraDrawRenderMode } from "terra-draw"
import { TerraDrawMapLibreGLAdapter } from "terra-draw-maplibre-gl-adapter"

const PREFIX = "apex-"

/* ── Layer Registry ── data-driven layer definitions for all engines ── */
interface LayerDef {
  sourceId: string
  layerIds: string[]
  color: string
  lineColor?: string
  opacity?: number
  /** "match" = categorical fill, "interpolate" = gradient, "simple" = solid color */
  fillMode?: "simple" | "match" | "interpolate"
  matchExpr?: any[]
  popupFn?: (props: any) => string
}

export const LAYER_REGISTRY: Record<string, LayerDef> = {
  def: {
    sourceId: PREFIX + "deforestation",
    layerIds: [PREFIX + "def-fill", PREFIX + "def-line", PREFIX + "def-highlight"],
    color: "#ef4444", lineColor: "#fca5a5", opacity: 0.45,
    popupFn: (p) => `<b style="color:#ef4444">Deforestacion</b><br>Area: ${p.area_ha ?? "?"} ha<br>Transicion a: ${p.transition_to ?? "?"}<br>Confianza: ${((p.confidence ?? 0) * 100).toFixed(0)}%${p.co2_tonnes ? `<br><b style="color:#34d399">CO₂: ${p.co2_tonnes} t</b> (AGBD: ${p.agbd_mg_ha ?? "?"} Mg/ha)` : ""}`,
  },
  str: {
    sourceId: PREFIX + "structures",
    layerIds: [PREFIX + "str-fill", PREFIX + "str-line", PREFIX + "str-highlight"],
    color: "#22d3ee", lineColor: "#22d3ee", opacity: 0.2,
    popupFn: (p) => `<b style="color:#22d3ee">Estructura</b><br>${p.note ?? p.type ?? "?"}`,
  },
  veg: {
    sourceId: PREFIX + "vegetation",
    layerIds: [PREFIX + "veg-fill", PREFIX + "veg-highlight"],
    color: "#22c55e", opacity: 0.6,
    fillMode: "match",
    matchExpr: ["match", ["get", "class"],
      "bosque_denso", "#166534", "bosque_ralo", "#22c55e",
      "pastizal", "#86efac", "suelo", "#92400e", "agua", "#3b82f6",
      "quemado", "#7c2d12", "urbano", "#6b21a8",
      "manglar_inundado", "#7a87c6", "cultivos", "#e49635",
      "matorral", "#dfc35a", "nieve", "#b39fe1",
      "#6b7280"],
    popupFn: (p) => `<b style="color:#22c55e">Vegetacion (Dynamic World)</b><br>Clase: ${p.class ?? "?"}<br>Area: ${p.area_ha ?? "?"} ha<br>Confianza: ${((p.confidence ?? 0) * 100).toFixed(0)}%`,
  },
  ue: {
    sourceId: PREFIX + "urban_expansion",
    layerIds: [PREFIX + "ue-fill", PREFIX + "ue-line", PREFIX + "ue-highlight"],
    color: "#f97316", lineColor: "#fed7aa", opacity: 0.55,
    popupFn: (p) => `<b style="color:#f97316">Expansion urbana detectada</b><br>Area: ${p.area_ha ?? "?"} ha<br>Origen: ${p.from_class ?? "?"} \u2192 ${p.to_class ?? "?"}<br>Tipo: ${p.expansion_type ?? "?"}<br><span style="color:${String(p.alerta ?? "").includes("sin permiso") ? "#ef4444" : "#86efac"}">${p.alerta ?? ""}</span>`,
  },
  hansen: {
    sourceId: PREFIX + "hansen",
    layerIds: [PREFIX + "hansen-fill", PREFIX + "hansen-line", PREFIX + "hansen-highlight"],
    color: "#facc15", lineColor: "#fde68a", opacity: 0.5,
    fillMode: "interpolate",
    popupFn: (p) => `<b style="color:#facc15">Hansen Forest Loss</b><br>Ano: ${p.loss_year ?? "?"}<br>Area: ${p.area_ha ?? "?"} ha<br>Cobertura original: ${p.original_treecover_pct ?? "?"}%<br>Confianza: ${((p.confidence ?? 0) * 100).toFixed(0)}%`,
  },
  alerts: {
    sourceId: PREFIX + "alerts",
    layerIds: [PREFIX + "alerts-fill", PREFIX + "alerts-line", PREFIX + "alerts-highlight"],
    color: "#dc2626", lineColor: "#fca5a5", opacity: 0.6,
    popupFn: (p) => `<b style="color:#dc2626">Alerta ${p.alert_type ?? "?"}</b><br>Fecha: ${p.alert_date ?? "?"}<br>Area: ${p.area_ha ?? "?"} ha<br>Confianza: ${p.confidence ?? "?"}`,
  },
  drivers: {
    sourceId: PREFIX + "drivers",
    layerIds: [PREFIX + "drivers-fill", PREFIX + "drivers-highlight"],
    color: "#8b5cf6", opacity: 0.5,
    fillMode: "match",
    matchExpr: ["match", ["get", "driver_label"],
      "Agricultura permanente", "#22c55e",
      "Commodities (mineria/energia)", "#f59e0b",
      "Cultivo rotacional", "#84cc16",
      "Tala", "#dc2626",
      "Incendios", "#ef4444",
      "Asentamientos e infraestructura", "#8b5cf6",
      "Perturbacion natural", "#6b7280",
      "#a78bfa"],
    popupFn: (p) => `<b style="color:#8b5cf6">Driver de deforestacion</b><br>Causa: ${p.driver_label ?? "?"}<br>Area: ${p.area_ha ?? "?"} ha`,
  },
  fire: {
    sourceId: PREFIX + "fire",
    layerIds: [PREFIX + "fire-fill", PREFIX + "fire-line", PREFIX + "fire-highlight"],
    color: "#f97316", lineColor: "#fdba74", opacity: 0.55,
    popupFn: (p) => `<b style="color:#f97316">Area quemada</b><br>Fecha: ${p.burn_date ?? "?"}<br>Area: ${p.area_ha ?? "?"} ha`,
  },
  anp: {
    sourceId: PREFIX + "anp",
    layerIds: [PREFIX + "anp-line", PREFIX + "anp-fill"],
    color: "#22c55e", lineColor: "#22c55e", opacity: 0.08,
    popupFn: (p) => `<b style="color:#22c55e">Area Natural Protegida</b><br>Nombre: ${p.name ?? p.ORIG_NAME ?? "?"}<br>Categoria: ${p.desig ?? p.IUCN_CAT ?? "?"}<br>Status: ${p.status ?? "?"}`,
  },
  sar: {
    sourceId: PREFIX + "sar",
    layerIds: [PREFIX + "sar-fill", PREFIX + "sar-line", PREFIX + "sar-highlight"],
    color: "#06b6d4", lineColor: "#67e8f9", opacity: 0.45,
    popupFn: (p) => `<b style="color:#06b6d4">Cambio SAR (Sentinel-1)</b><br>Area: ${p.area_ha ?? "?"} ha<br>Cambio VV: ${p.change_db_vv ?? "?"} dB<br>Cambio VH: ${p.change_db_vh ?? "?"} dB<br>Confianza: ${p.confidence ?? "?"}`,
  },
  firms_hotspots: {
    sourceId: PREFIX + "firms_hotspots",
    layerIds: [PREFIX + "firms_hotspots-circle", PREFIX + "firms_hotspots-heat", PREFIX + "firms_hotspots-cluster-fill"],
    color: "#ff3b30", lineColor: "#ff6b6b", opacity: 0.8,
    popupFn: (p) => p.type === "fire_cluster"
      ? `<b style="color:#ff3b30">Agrupacion de incendios</b><br>Detecciones: ${p.detection_count ?? "?"}<br>FRP total: ${p.total_frp_mw ?? "?"} MW<br>Satelites: ${Array.isArray(p.satellites) ? p.satellites.join(", ") : (p.satellites ?? "?")}<br>Fechas: ${p.date_range ?? "?"}`
      : `<b style="color:#ff3b30">Hotspot FIRMS</b><br>FRP: ${p.frp_mw ?? "?"} MW<br>Confianza: ${p.confidence_label ?? "?"}<br>Satelite: ${p.satellite ?? "?"}<br>Fecha: ${p.acq_datetime ?? "?"}<br>Fuente: ${p.source ?? "?"}`,
  },
  avocado: {
    sourceId: PREFIX + "avocado",
    layerIds: [PREFIX + "avocado-fill", PREFIX + "avocado-line", PREFIX + "avocado-highlight"],
    color: "#a855f7", lineColor: "#c084fc", opacity: 0.5,
    fillMode: "match",
    matchExpr: ["match", ["get", "severity"],
      "critica", "#dc2626",
      "alta", "#f97316",
      "media", "#facc15",
      "baja", "#a3e635",
      "#a855f7"],
    popupFn: (p) => `<b style="color:#a855f7">Anomalía NDVI (AVOCADO)</b><br>Severidad: ${p.severity ?? "?"}<br>Área: ${(p.area_ha ?? 0).toFixed(2)} ha<br>ΔNDVI promedio: ${(p.mean ?? 0).toFixed(3)}<br>ΔNDVI mín: ${(p.min ?? 0).toFixed(3)}`,
  },
  spectralgpt: {
    sourceId: PREFIX + "spectralgpt",
    layerIds: [PREFIX + "spectralgpt-fill", PREFIX + "spectralgpt-line", PREFIX + "spectralgpt-highlight"],
    color: "#14b8a6", lineColor: "#5eead4", opacity: 0.5,
    fillMode: "match",
    matchExpr: ["match", ["get", "class"],
      "bosque_denso", "#166534", "bosque_ralo", "#22c55e",
      "pastizal", "#86efac", "cultivos", "#e49635",
      "matorral", "#dfc35a", "urbano", "#6b21a8",
      "suelo", "#92400e", "agua", "#3b82f6",
      "manglar_inundado", "#7a87c6", "quemado", "#7c2d12",
      "#14b8a6"],
    popupFn: (p) => `<b style="color:#14b8a6">SpectralGPT LULC</b><br>Clase: ${p.class ?? "?"}<br>Área: ${p.area_ha ?? "?"} ha<br>Confianza: ${((p.confidence ?? 0) * 100).toFixed(0)}%<br>Modelo: ${p.model ?? "?"}`,
  },
  drivers_mx: {
    sourceId: PREFIX + "drivers_mx",
    layerIds: [PREFIX + "drivers_mx-fill", PREFIX + "drivers_mx-line", PREFIX + "drivers_mx-highlight"],
    color: "#c084fc", lineColor: "#e9d5ff", opacity: 0.55,
    fillMode: "match",
    matchExpr: ["match", ["get", "driver_mx"],
      "ganaderia", "#d97706", "agricultura", "#65a30d",
      "expansion_urbana", "#f43f5e", "incendio", "#ef4444",
      "tala_ilegal", "#7c3aed", "infraestructura", "#6b7280",
      "plantacion", "#059669", "natural", "#0ea5e9",
      "#c084fc"],
    popupFn: (p) => `<b style="color:#c084fc">ForestNet-MX</b><br>Driver: ${p.driver_mx_label ?? p.driver_mx ?? "?"}<br>Área: ${p.area_ha ?? "?"} ha`,
  },
}

/** Engine key → backend result key mapping */
const ENGINE_TO_RESULT: Record<string, string> = {
  def: "deforestation", str: "structures", veg: "vegetation", ue: "urban_expansion",
  hansen: "hansen", alerts: "alerts", drivers: "drivers", fire: "fire",
  anp: "legal_context", sar: "sar", firms_hotspots: "firms_hotspots",
  drivers_mx: "deforestation",
}

const ALL_LAYER_IDS = Object.values(LAYER_REGISTRY).flatMap((r) => r.layerIds)
const SOURCE_IDS = Object.values(LAYER_REGISTRY).map((r) => r.sourceId)

export type LayerKey = keyof typeof LAYER_REGISTRY

/* Basemaps: raster tiles to avoid Carto vector-tile CORS issues */
const _rasterStyle = (
  tiles: string[],
  sourceId = "basemap",
  layerId = "basemap-layer",
): maplibregl.StyleSpecification => ({
  version: 8,
  sources: { [sourceId]: { type: "raster", tiles, tileSize: 256 } },
  layers: [{ id: layerId, type: "raster", source: sourceId }],
})

export const BASEMAPS: Record<string, maplibregl.StyleSpecification> = {
  "Oscuro (Carto)": _rasterStyle([
    "https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png",
    "https://b.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png",
    "https://c.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png",
  ]),
  "Claro (Carto)": _rasterStyle([
    "https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png",
    "https://b.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png",
    "https://c.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png",
  ]),
  "Satelite (Esri)": _rasterStyle([
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
  ]),
  "Voyager (Carto)": _rasterStyle([
    "https://a.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}@2x.png",
    "https://b.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}@2x.png",
    "https://c.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}@2x.png",
  ]),
  "Hibrido (Esri)": {
    version: 8,
    sources: {
      esri: {
        type: "raster",
        tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
        tileSize: 256,
      },
      labels: {
        type: "raster",
        tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}"],
        tileSize: 256,
      },
    },
    layers: [
      { id: "esri-satellite", type: "raster", source: "esri" },
      { id: "esri-labels", type: "raster", source: "labels" },
    ],
  },
}

export type DrawMode = "static" | "polygon" | "select"

export interface MapViewHandle {
  getMap: () => maplibregl.Map | null
  highlightFeature: (sourceId: string, featureId: number | null) => void
  flyToCoord: (lng: number, lat: number) => void
  toggleLayerVisibility: (key: LayerKey, visible: boolean) => void
  renderYearLayers: (year: number, yearData: any) => void
  clearYearLayers: () => void
  renderForecastLayers: (spatialForecast: { deforestation?: any; urban_expansion?: any }) => void
  clearForecastLayers: () => void
  clearAOI: () => void
  setDrawMode: (mode: DrawMode) => void
  renderUploadedPolygons: (polygons: { id: string; feature_collection: any; color: string; visible: boolean }[]) => void
  flyToBbox: (bbox: number[]) => void
  setAoiFromGeometry: (geom: any) => void
}

interface MapViewProps {
  basemap: string
  results: any | null
  onAoiChange: (aoi: object | null) => void
  onPolygonDrawn?: (geometry: any) => void
  onDrawModeChange?: (mode: DrawMode) => void
  drawMode?: DrawMode
}

const MapView = forwardRef<MapViewHandle, MapViewProps>(function MapView({ basemap, results, onAoiChange, onPolygonDrawn, onDrawModeChange, drawMode = "static" }, ref) {
  const mapContainer = useRef<HTMLDivElement>(null)
  const map = useRef<maplibregl.Map | null>(null)
  const drawRef = useRef<TerraDraw | null>(null)
  const resultsRef = useRef<any>(null)
  const lastHighlighted = useRef<{ source: string; id: number } | null>(null)
  const activePopup = useRef<maplibregl.Popup | null>(null)
  const aoiRef = useRef<object | null>(null)
  const basemapInitRef = useRef(true) // skip first basemap effect (handled by init)
  const [timelineToast, setTimelineToast] = useState<string | null>(null)

  resultsRef.current = results

  // Stable refs for callbacks used inside initTerraDraw
  const onPolygonDrawnRef = useRef(onPolygonDrawn)
  onPolygonDrawnRef.current = onPolygonDrawn
  const onDrawModeChangeRef = useRef(onDrawModeChange)
  onDrawModeChangeRef.current = onDrawModeChange

  // ---- clear layers ----
  const clearResultLayers = useCallback(() => {
    if (!map.current) return
    ALL_LAYER_IDS.forEach((id) => { if (map.current!.getLayer(id)) map.current!.removeLayer(id) })
    SOURCE_IDS.forEach((id) => { if (map.current!.getSource(id)) map.current!.removeSource(id) })
  }, [])

  // ---- add result layers ----
  const addResultLayers = useCallback((data: any) => {
    if (!map.current || !data?.layers) return
    clearResultLayers()

    const addPopup = (layerId: string, htmlFn: (props: any) => string) => {
      map.current!.on("click", layerId, (e) => {
        const props = e.features?.[0]?.properties
        if (!props) return
        if (activePopup.current) activePopup.current.remove()
        activePopup.current = new maplibregl.Popup({ closeButton: true, maxWidth: "240px" })
          .setLngLat(e.lngLat).setHTML(htmlFn(props)).addTo(map.current!)
      })
      map.current!.on("mouseenter", layerId, () => { map.current!.getCanvas().style.cursor = "pointer" })
      map.current!.on("mouseleave", layerId, () => { map.current!.getCanvas().style.cursor = "" })
    }

    // Data-driven layer rendering from registry
    for (const [key, reg] of Object.entries(LAYER_REGISTRY)) {
      const resultKey = ENGINE_TO_RESULT[key] ?? key
      const geo = data.layers[resultKey]?.geojson
      if (!geo || !geo.features?.length) continue

      map.current.addSource(reg.sourceId, { type: "geojson", data: geo, generateId: true })

      // ── Special rendering for FIRMS point-based hotspots ──
      if (key === "firms_hotspots") {
        // Circle layer for point hotspots (radius = FRP intensity)
        const circleId = reg.layerIds.find((id) => id.endsWith("-circle"))
        if (circleId) {
          map.current.addLayer({
            id: circleId, type: "circle", source: reg.sourceId,
            filter: ["==", ["geometry-type"], "Point"],
            paint: {
              "circle-radius": ["interpolate", ["linear"], ["get", "frp_mw"],
                0, 4, 50, 8, 200, 14, 500, 20] as any,
              "circle-color": ["interpolate", ["linear"], ["get", "confidence"],
                0.3, "#ffcc00", 0.6, "#ff6600", 0.9, "#ff0000"] as any,
              "circle-opacity": 0.85,
              "circle-stroke-width": 1.5,
              "circle-stroke-color": "#ffffff88",
            },
          })
          if (reg.popupFn) addPopup(circleId, reg.popupFn)
        }
        // Fill layer for cluster polygons
        const clusterFillId = reg.layerIds.find((id) => id.endsWith("-cluster-fill"))
        if (clusterFillId) {
          map.current.addLayer({
            id: clusterFillId, type: "fill", source: reg.sourceId,
            filter: ["!=", ["geometry-type"], "Point"],
            paint: {
              "fill-color": "#ff3b30",
              "fill-opacity": 0.2,
            },
          })
          if (reg.popupFn) addPopup(clusterFillId, reg.popupFn)
        }
        // Heatmap layer (low zoom levels)
        const heatId = reg.layerIds.find((id) => id.endsWith("-heat"))
        if (heatId) {
          map.current.addLayer({
            id: heatId, type: "heatmap", source: reg.sourceId,
            filter: ["==", ["geometry-type"], "Point"],
            maxzoom: 11,
            paint: {
              "heatmap-weight": ["interpolate", ["linear"], ["get", "frp_mw"], 0, 0.1, 100, 0.5, 500, 1] as any,
              "heatmap-intensity": ["interpolate", ["linear"], ["zoom"], 0, 0.5, 11, 2] as any,
              "heatmap-radius": ["interpolate", ["linear"], ["zoom"], 0, 8, 11, 25] as any,
              "heatmap-color": [
                "interpolate", ["linear"], ["heatmap-density"],
                0, "rgba(0,0,0,0)", 0.2, "#ffe234", 0.4, "#ffa020",
                0.6, "#ff6600", 0.8, "#ff2200", 1, "#cc0000",
              ] as any,
              "heatmap-opacity": ["interpolate", ["linear"], ["zoom"], 7, 0.8, 11, 0.3] as any,
            },
          })
        }
        continue  // skip generic fill/line/highlight
      }

      // Fill layer
      const fillId = reg.layerIds.find((id) => id.endsWith("-fill"))
      if (fillId) {
        const fillColor =
          reg.fillMode === "match" ? reg.matchExpr :
          reg.fillMode === "interpolate"
            ? ["interpolate", ["linear"], ["get", "loss_year"],
                2001, "#fef08a", 2012, "#f97316", 2024, "#dc2626"]
            : reg.color
        map.current.addLayer({
          id: fillId, type: "fill", source: reg.sourceId,
          paint: {
            "fill-color": fillColor as any,
            "fill-opacity": reg.opacity ?? 0.45,
          },
        })
      }

      // Line layer
      const lineId = reg.layerIds.find((id) => id.endsWith("-line"))
      if (lineId) {
        const isAnp = key === "anp"
        map.current.addLayer({
          id: lineId, type: "line", source: reg.sourceId,
          paint: {
            "line-color": reg.lineColor ?? reg.color,
            "line-width": isAnp ? 2.5 : 1.5,
            ...(isAnp ? { "line-dasharray": [4, 3] as any } : {}),
          },
        })
      }

      // Highlight layer
      const highlightId = reg.layerIds.find((id) => id.endsWith("-highlight"))
      if (highlightId) {
        map.current.addLayer({
          id: highlightId, type: "line", source: reg.sourceId,
          paint: { "line-color": "#ffffff", "line-width": ["case", ["boolean", ["feature-state", "highlighted"], false], 3, 0] as any },
        })
      }

      // Popup
      if (fillId && reg.popupFn && map.current.getLayer(fillId)) {
        addPopup(fillId, reg.popupFn)
      }
    }
  }, [clearResultLayers])

  // ---- render AOI outline ----
  const renderAOI = useCallback(() => {
    if (!map.current || !aoiRef.current) return
    if (map.current.getLayer("aoi-fill")) map.current.removeLayer("aoi-fill")
    if (map.current.getLayer("aoi-line")) map.current.removeLayer("aoi-line")
    if (map.current.getSource("aoi")) map.current.removeSource("aoi")
    map.current.addSource("aoi", {
      type: "geojson",
      data: { type: "Feature", geometry: aoiRef.current as any, properties: {} }
    })
    map.current.addLayer({ id: "aoi-fill", type: "fill", source: "aoi",
      paint: { "fill-color": "#22d3ee", "fill-opacity": 0.08 } })
    map.current.addLayer({ id: "aoi-line", type: "line", source: "aoi",
      paint: { "line-color": "#22d3ee", "line-width": 2, "line-dasharray": [3, 2] } })
  }, [])

  // ---- create / recreate TerraDraw instance ----
  const initTerraDraw = useCallback(() => {
    if (!map.current) return
    // Destroy previous instance if any
    if (drawRef.current) {
      try { drawRef.current.stop() } catch { /* may already be stopped */ }
      drawRef.current = null
    }
    console.log("[APEX-Draw] Creating new TerraDraw instance")
    const draw = new TerraDraw({
      adapter: new TerraDrawMapLibreGLAdapter({ map: map.current! }),
      modes: [
        new TerraDrawPolygonMode(),
        new TerraDrawSelectMode({
          flags: {
            polygon: {
              feature: { draggable: true, coordinates: { midpoints: true, draggable: true, deletable: true } },
            },
          },
        }),
        new TerraDrawRenderMode({ modeName: "static", styles: {} }),
      ],
    })
    draw.start()
    draw.setMode("static")
    drawRef.current = draw
    console.log("[APEX-Draw] TerraDraw started, mode=static")

    draw.on("finish", () => {
      const snapshot = draw.getSnapshot()
      if (snapshot.length > 0) {
        const geom = snapshot[snapshot.length - 1].geometry as any
        const nVerts = geom?.coordinates?.[0]?.length ?? 0
        console.log("[APEX] Poligono dibujado:", nVerts, "vertices")
        aoiRef.current = geom
        onAoiChange(geom)
        renderAOI()

        // Notify parent so polygon gets added to the manager
        onPolygonDrawnRef.current?.(geom)

        // Switch back to static (navigation) mode after drawing
        try {
          draw.clear()
          draw.setMode("static")
          console.log("[APEX-Draw] Switched to static mode after polygon finish")
        } catch { /* ignore */ }
        onDrawModeChangeRef.current?.("static")
      }
    })
    draw.on("change", () => {
      if (draw.getMode() !== "select") return
      const snapshot = draw.getSnapshot()
      if (snapshot.length > 0) {
        const geom = snapshot[snapshot.length - 1].geometry as any
        if (geom?.type === "Polygon" && geom.coordinates?.[0]?.length >= 4) {
          aoiRef.current = geom
          onAoiChange(geom)
          renderAOI()
        }
      }
    })
  }, [onAoiChange, renderAOI])

  // ---- map init ----
  useEffect(() => {
    if (map.current || !mapContainer.current) return
    console.log("[APEX-Map] Initializing map with basemap:", basemap)
    map.current = new maplibregl.Map({
      container: mapContainer.current,
      style: BASEMAPS[basemap] as any,
      center: [-89.65, 20.5],
      zoom: 9,
    })
    map.current.on("load", () => {
      console.log("[APEX-Map] Initial load complete")
      initTerraDraw()
    })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ---- basemap switch ----
  useEffect(() => {
    // Skip the first render — init effect handles it
    if (basemapInitRef.current) {
      basemapInitRef.current = false
      return
    }
    if (!map.current) return
    console.log("[APEX-Map] Switching basemap to:", basemap)

    // Destroy terra-draw completely — its internal sources/layers will be
    // wiped when setStyle replaces the style
    if (drawRef.current) {
      try { drawRef.current.stop() } catch { /* already stopped */ }
      drawRef.current = null
      console.log("[APEX-Draw] Destroyed old TerraDraw before style swap")
    }

    map.current.setStyle(BASEMAPS[basemap] as any, { diff: false })

    // "style.load" always fires after setStyle, unlike "load" which may not
    map.current.once("style.load", () => {
      console.log("[APEX-Map] style.load fired — reattaching layers")
      initTerraDraw()
      renderAOI()
      if (resultsRef.current) addResultLayers(resultsRef.current)
      console.log("[APEX-Map] Basemap switch complete — draw should be active")
    })
  }, [basemap, addResultLayers, renderAOI, initTerraDraw])

  // ---- render results ----
  useEffect(() => {
    if (!map.current || !results) return
    const apply = () => {
      console.log("[APEX-Map] Rendering result layers")
      addResultLayers(results)
    }
    if (!map.current.isStyleLoaded()) {
      console.log("[APEX-Map] Style not loaded yet — deferring result render")
      map.current.once("style.load", apply)
      return
    }
    apply()
  }, [results, addResultLayers])

  // ---- imperative handle for parent ----
  const highlightFeature = useCallback((sourceId: string, featureId: number | null) => {
    if (!map.current) return
    if (lastHighlighted.current) {
      try { map.current.setFeatureState(lastHighlighted.current, { highlighted: false }) } catch { /* noop */ }
      lastHighlighted.current = null
    }
    if (featureId !== null && map.current.getSource(sourceId)) {
      map.current.setFeatureState({ source: sourceId, id: featureId }, { highlighted: true })
      lastHighlighted.current = { source: sourceId, id: featureId }
    }
  }, [])

  const flyToCoord = useCallback((lng: number, lat: number) => {
    map.current?.flyTo({ center: [lng, lat], zoom: 14, duration: 1200 })
  }, [])

  const toggleLayerVisibility = useCallback((key: LayerKey, visible: boolean) => {
    if (!map.current) return
    const vis = visible ? "visible" : "none"
    const reg = LAYER_REGISTRY[key]
    if (!reg) return
    reg.layerIds.forEach((id) => { if (map.current!.getLayer(id)) map.current!.setLayoutProperty(id, "visibility", vis) })
  }, [])

  const YEAR_LAYERS = ["year-def-fill","year-def-line","year-ue-fill","year-ue-line","year-veg-fill"]
  const YEAR_SOURCES = ["year-deforestation","year-urban","year-vegetation"]

  const FORECAST_LAYERS = ["fc-def-fill","fc-def-line","fc-ue-fill","fc-ue-line"]
  const FORECAST_SOURCES = ["fc-deforestation","fc-urban"]

  const clearYearLayers = useCallback(() => {
    if (!map.current) return
    YEAR_LAYERS.forEach(id => { if (map.current!.getLayer(id)) map.current!.removeLayer(id) })
    YEAR_SOURCES.forEach(id => { if (map.current!.getSource(id)) map.current!.removeSource(id) })
  }, [])

  const clearForecastLayers = useCallback(() => {
    if (!map.current) return
    FORECAST_LAYERS.forEach(id => { if (map.current!.getLayer(id)) map.current!.removeLayer(id) })
    FORECAST_SOURCES.forEach(id => { if (map.current!.getSource(id)) map.current!.removeSource(id) })
  }, [])

  const renderForecastLayers = useCallback((spatialForecast: { deforestation?: any; urban_expansion?: any }) => {
    if (!map.current || !spatialForecast) return

    // Clear previous forecast layers
    FORECAST_LAYERS.forEach(id => { if (map.current!.getLayer(id)) map.current!.removeLayer(id) })
    FORECAST_SOURCES.forEach(id => { if (map.current!.getSource(id)) map.current!.removeSource(id) })

    // Deforestation forecast — purple dashed
    const geoD = spatialForecast.deforestation
    if (geoD?.features?.length > 0) {
      map.current.addSource("fc-deforestation", { type: "geojson", data: geoD })
      map.current.addLayer({
        id: "fc-def-fill", type: "fill", source: "fc-deforestation",
        paint: {
          "fill-color": ["match", ["get", "risk"],
            "CRITICAL", "#dc2626",
            "HIGH", "#ea580c",
            "MEDIUM", "#d97706",
            "#65a30d"] as any,
          "fill-opacity": 0.25,
        },
      })
      map.current.addLayer({
        id: "fc-def-line", type: "line", source: "fc-deforestation",
        paint: {
          "line-color": "#a855f7",
          "line-width": 2.5,
          "line-dasharray": [4, 3],
        },
      })
    }

    // Urban expansion forecast — cyan dashed
    const geoU = spatialForecast.urban_expansion
    if (geoU?.features?.length > 0) {
      map.current.addSource("fc-urban", { type: "geojson", data: geoU })
      map.current.addLayer({
        id: "fc-ue-fill", type: "fill", source: "fc-urban",
        paint: {
          "fill-color": ["match", ["get", "risk"],
            "CRITICAL", "#dc2626",
            "HIGH", "#ea580c",
            "MEDIUM", "#d97706",
            "#65a30d"] as any,
          "fill-opacity": 0.2,
        },
      })
      map.current.addLayer({
        id: "fc-ue-line", type: "line", source: "fc-urban",
        paint: {
          "line-color": "#06b6d4",
          "line-width": 2.5,
          "line-dasharray": [4, 3],
        },
      })
    }

    // Popups for forecast layers
    const addFcPopup = (layerId: string) => {
      map.current!.on("click", layerId, (e) => {
        const p = e.features?.[0]?.properties
        if (!p) return
        if (activePopup.current) activePopup.current.remove()
        const riskColors: Record<string, string> = { CRITICAL: "#dc2626", HIGH: "#ea580c", MEDIUM: "#d97706", LOW: "#65a30d" }
        const color = riskColors[p.risk] || "#a855f7"
        activePopup.current = new maplibregl.Popup({ maxWidth: "220px" })
          .setLngLat(e.lngLat)
          .setHTML(
            `<b style="color:${color}">Predicción ${p.year}</b><br>` +
            `Área proyectada: <b>${p.predicted_ha} ha</b><br>` +
            `Riesgo: <b style="color:${color}">${p.risk}</b><br>` +
            `<span style="font-size:11px;color:#888">${p.years_ahead} año(s) en el futuro</span>`
          )
          .addTo(map.current!)
      })
      map.current!.on("mouseenter", layerId, () => { map.current!.getCanvas().style.cursor = "pointer" })
      map.current!.on("mouseleave", layerId, () => { map.current!.getCanvas().style.cursor = "" })
    }
    if (map.current.getLayer("fc-def-fill")) addFcPopup("fc-def-fill")
    if (map.current.getLayer("fc-ue-fill")) addFcPopup("fc-ue-fill")

    // Fly to forecast area
    try {
      const allFeats = [...(geoD?.features || []), ...(geoU?.features || [])]
      if (allFeats.length > 0) {
        const coords: number[][] = []
        const extractCoords = (g: any) => {
          if (!g) return
          if (g.type === "Polygon") g.coordinates.forEach((ring: number[][]) => coords.push(...ring))
          else if (g.type === "MultiPolygon") g.coordinates.forEach((poly: number[][][]) => poly.forEach((ring: number[][]) => coords.push(...ring)))
        }
        allFeats.forEach((f: any) => extractCoords(f.geometry))
        if (coords.length > 0) {
          const lngs = coords.map(c => c[0])
          const lats = coords.map(c => c[1])
          map.current.fitBounds(
            [[Math.min(...lngs), Math.min(...lats)], [Math.max(...lngs), Math.max(...lats)]],
            { padding: 80, duration: 1200 }
          )
        }
      }
    } catch { /* ignore fitBounds errors */ }

    setTimelineToast("Mostrando predicción espacial en el mapa")
    setTimeout(() => setTimelineToast(null), 4000)
  }, [])

  // ── Clear AOI and terra-draw features ──
  const clearAOI = useCallback(() => {
    if (!map.current) return
    console.log("[APEX-Draw] clearAOI called, drawRef exists:", !!drawRef.current)
    // Remove AOI layers
    if (map.current.getLayer("aoi-fill")) map.current.removeLayer("aoi-fill")
    if (map.current.getLayer("aoi-line")) map.current.removeLayer("aoi-line")
    if (map.current.getSource("aoi")) map.current.removeSource("aoi")
    // Clear terra-draw (guard against disabled state during basemap switch)
    if (drawRef.current) {
      try {
        drawRef.current.clear()
        drawRef.current.setMode("static")
        console.log("[APEX-Draw] Cleared and reset to static mode")
      } catch (err) {
        console.warn("[APEX-Draw] clearAOI: TerraDraw error (may be stopped):", (err as Error).message)
      }
    }
    aoiRef.current = null
    onAoiChange(null)
  }, [onAoiChange])

  // ── Set draw mode ──
  const setDrawMode = useCallback((mode: DrawMode) => {
    if (!drawRef.current) {
      console.warn("[APEX-Draw] setDrawMode: no TerraDraw instance")
      return
    }
    try {
      drawRef.current.setMode(mode)
      console.log("[APEX-Draw] Mode set to:", mode)
    } catch (err) {
      console.warn("[APEX-Draw] setDrawMode error:", (err as Error).message)
    }
  }, [])

  // Sync drawMode prop
  useEffect(() => {
    if (!drawRef.current) return
    try { drawRef.current.setMode(drawMode) } catch { /* ignore */ }
  }, [drawMode])

  const renderYearLayers = useCallback((year: number, yearData: any) => {
    if (!map.current) return

    // Clear previous year layers
    YEAR_LAYERS.forEach(id => { if (map.current!.getLayer(id)) map.current!.removeLayer(id) })
    YEAR_SOURCES.forEach(id => { if (map.current!.getSource(id)) map.current!.removeSource(id) })

    // 1. Vegetation background (year T2) — all DW classes
    const geoVeg = yearData?.vegetation?.geojson
    if (geoVeg?.features?.length > 0) {
      map.current.addSource("year-vegetation", { type: "geojson", data: geoVeg })
      map.current.addLayer({ id: "year-veg-fill", type: "fill", source: "year-vegetation",
        paint: {
          "fill-opacity": 0.55,
          "fill-color": ["match", ["get", "class"],
            "bosque_denso", "#166534", "bosque_ralo", "#22c55e",
            "pastizal", "#86efac", "suelo", "#92400e", "agua", "#3b82f6",
            "quemado", "#7c2d12", "urbano", "#6b21a8",
            "manglar_inundado", "#7a87c6", "cultivos", "#e49635",
            "matorral", "#dfc35a", "nieve", "#b39fe1",
            "#6b7280"] as any,
        } })
    }

    // 2. Deforestation on top (change from baseline)
    const geoD = yearData?.deforestation?.geojson
    if (geoD?.features?.length > 0) {
      map.current.addSource("year-deforestation", { type: "geojson", data: geoD })
      map.current.addLayer({ id: "year-def-fill", type: "fill", source: "year-deforestation",
        paint: { "fill-color": "#ef4444", "fill-opacity": 0.7 } })
      map.current.addLayer({ id: "year-def-line", type: "line", source: "year-deforestation",
        paint: { "line-color": "#fca5a5", "line-width": 1.5 } })
    }

    // 3. Urban expansion on top
    const geoU = yearData?.urban_expansion?.geojson
    if (geoU?.features?.length > 0) {
      map.current.addSource("year-urban", { type: "geojson", data: geoU })
      map.current.addLayer({ id: "year-ue-fill", type: "fill", source: "year-urban",
        paint: { "fill-color": "#f97316", "fill-opacity": 0.7 } })
      map.current.addLayer({ id: "year-ue-line", type: "line", source: "year-urban",
        paint: { "line-color": "#fed7aa", "line-width": 1.5 } })
    }

    // Popups for year layers
    const addYearPopup = (layerId: string, htmlFn: (props: any) => string) => {
      map.current!.on("click", layerId, (e) => {
        const p = e.features?.[0]?.properties
        if (!p) return
        if (activePopup.current) activePopup.current.remove()
        activePopup.current = new maplibregl.Popup({ maxWidth: "200px" })
          .setLngLat(e.lngLat).setHTML(htmlFn(p)).addTo(map.current!)
      })
      map.current!.on("mouseenter", layerId, () => { map.current!.getCanvas().style.cursor = "pointer" })
      map.current!.on("mouseleave", layerId, () => { map.current!.getCanvas().style.cursor = "" })
    }
    if (map.current.getLayer("year-veg-fill"))
      addYearPopup("year-veg-fill", (p) =>
        `<b>${year} \u2014 Vegetacion</b><br>Clase: ${p.class ?? "?"}<br>Area: ${p.area_ha ?? "?"} ha<br>Confianza: ${Math.round((p.confidence ?? 0) * 100)}%`)
    if (map.current.getLayer("year-def-fill"))
      addYearPopup("year-def-fill", (p) =>
        `<b style="color:#ef4444">${year} \u2014 Deforestacion</b><br>Area: ${p.area_ha ?? "?"} ha<br>${p.transition_to ?? ""}`)
    if (map.current.getLayer("year-ue-fill"))
      addYearPopup("year-ue-fill", (p) =>
        `<b style="color:#f97316">${year} \u2014 Exp. urbana</b><br>Area: ${p.area_ha ?? "?"} ha<br>${p.from_class ?? ""} \u2192 ${p.to_class ?? ""}`)

    setTimelineToast(`Mostrando cambios ${yearData.baseline_year} \u2192 ${year}`)
    setTimeout(() => setTimelineToast(null), 3000)
  }, [])

  // ── Render uploaded polygon layers ──
  const uploadedSourceIds = useRef<string[]>([])
  const uploadedLayerIds = useRef<string[]>([])

  const renderUploadedPolygons = useCallback((polygons: { id: string; feature_collection: any; color: string; visible: boolean }[]) => {
    if (!map.current) return

    // Remove old uploaded layers
    uploadedLayerIds.current.forEach((id) => { if (map.current!.getLayer(id)) map.current!.removeLayer(id) })
    uploadedSourceIds.current.forEach((id) => { if (map.current!.getSource(id)) map.current!.removeSource(id) })
    uploadedSourceIds.current = []
    uploadedLayerIds.current = []

    // Add new layers
    for (const poly of polygons) {
      const srcId = `upload-${poly.id}`
      const fillId = `upload-${poly.id}-fill`
      const lineId = `upload-${poly.id}-line`

      map.current.addSource(srcId, { type: "geojson", data: poly.feature_collection })
      map.current.addLayer({
        id: fillId, type: "fill", source: srcId,
        paint: { "fill-color": poly.color, "fill-opacity": poly.visible ? 0.2 : 0 },
        layout: { visibility: poly.visible ? "visible" : "none" },
      })
      map.current.addLayer({
        id: lineId, type: "line", source: srcId,
        paint: { "line-color": poly.color, "line-width": 2, "line-dasharray": [4, 2] },
        layout: { visibility: poly.visible ? "visible" : "none" },
      })

      uploadedSourceIds.current.push(srcId)
      uploadedLayerIds.current.push(fillId, lineId)

      // Popup on click
      map.current.on("click", fillId, (e) => {
        const props = e.features?.[0]?.properties
        if (!props) return
        if (activePopup.current) activePopup.current.remove()
        activePopup.current = new maplibregl.Popup({ closeButton: true, maxWidth: "200px" })
          .setLngLat(e.lngLat)
          .setHTML(`<b style="color:${poly.color}">Poligono cargado</b><br>${props.source_file ?? ""}`)
          .addTo(map.current!)
      })
      map.current.on("mouseenter", fillId, () => { map.current!.getCanvas().style.cursor = "pointer" })
      map.current.on("mouseleave", fillId, () => { map.current!.getCanvas().style.cursor = "" })
    }
  }, [])

  // ── Fly to bounding box ──
  const flyToBbox = useCallback((bbox: number[]) => {
    if (!map.current || bbox.length < 4) return
    map.current.fitBounds(
      [[bbox[0], bbox[1]], [bbox[2], bbox[3]]],
      { padding: 60, duration: 1200 }
    )
  }, [])

  // ── Set AOI from uploaded geometry ──
  const setAoiFromGeometry = useCallback((geom: any) => {
    if (!map.current || !geom) return
    aoiRef.current = geom
    onAoiChange(geom)
    renderAOI()

    // Fly to the geometry bbox
    const coords = geom.coordinates?.[0]
    if (coords?.length > 0) {
      const lngs = coords.map((c: number[]) => c[0])
      const lats = coords.map((c: number[]) => c[1])
      map.current.fitBounds(
        [[Math.min(...lngs), Math.min(...lats)], [Math.max(...lngs), Math.max(...lats)]],
        { padding: 60, duration: 1200 }
      )
    }

    // Clear terra-draw features and switch to static mode
    if (drawRef.current) {
      try {
        drawRef.current.clear()
        drawRef.current.setMode("static")
      } catch { /* ignore */ }
    }
  }, [onAoiChange, renderAOI])

  useImperativeHandle(ref, () => ({
    getMap: () => map.current,
    highlightFeature,
    flyToCoord,
    toggleLayerVisibility,
    renderYearLayers,
    clearYearLayers,
    renderForecastLayers,
    clearForecastLayers,
    clearAOI,
    setDrawMode,
    renderUploadedPolygons,
    flyToBbox,
    setAoiFromGeometry,
  }), [highlightFeature, flyToCoord, toggleLayerVisibility, renderYearLayers, clearYearLayers, renderForecastLayers, clearForecastLayers, clearAOI, setDrawMode, renderUploadedPolygons, flyToBbox, setAoiFromGeometry])

  return (
    <div className="relative w-full h-full">
      <div ref={mapContainer} className="w-full h-full" />

      {/* timeline toast */}
      {timelineToast && (
        <div className="absolute top-4 left-1/2 -translate-x-1/2 z-50 bg-yellow-600 text-white px-5 py-2 rounded-lg shadow-lg text-sm font-medium animate-pulse">
          {timelineToast}
        </div>
      )}
    </div>
  )
})

export default MapView