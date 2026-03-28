import { useCallback, useEffect, useRef, useState } from "react"
import MapView, { type MapViewHandle, type LayerKey, type DrawMode } from "./components/MapView"
import Sidebar from "./components/Sidebar"
import TopBar from "./components/TopBar"
import ValidationPanel from "./components/ValidationPanel"
import LegendPanel from "./components/LegendPanel"
import LoginPage from "./components/LoginPage"
import StrategicPanel from "./components/StrategicPanel"
import SimulatorPanel from "./components/SimulatorPanel"
import ImpactDashboard from "./components/ImpactDashboard"
import type { UploadedPolygon } from "./components/PolygonManager"
import { API_BASE_URL } from "./config"

const C = {
  bgBase: "#0d1117",
  bgPanel: "#161b22",
  border: "#30363d",
  text2: "#8b949e",
} as const

function App() {
  const mapRef = useRef<MapViewHandle>(null)

  // ── Auth state ──
  const [token, setToken] = useState<string | null>(() => localStorage.getItem("apex_token"))
  const [user, setUser] = useState<{ email: string; role: string; full_name: string } | null>(() => {
    try { return JSON.parse(localStorage.getItem("apex_user") || "null") } catch { return null }
  })
  const [rightPanel, setRightPanel] = useState<"none" | "strategic" | "simulator" | "impact">("none")

  const handleLogin = (t: string, u: { email: string; role: string; full_name: string }) => {
    setToken(t)
    setUser(u)
  }
  const handleLogout = () => {
    localStorage.removeItem("apex_token")
    localStorage.removeItem("apex_user")
    setToken(null)
    setUser(null)
  }

  // If no token, show login
  if (!token) return <LoginPage onLogin={handleLogin} />

  // ── Lifted state ──
  const [aoi, setAoi] = useState<object | null>(null)
  const [engines, setEngines] = useState<string[]>(["deforestation", "vegetation", "structures", "urban_expansion"])
  const [basemap, setBasemap] = useState("Oscuro (Carto)")
  const [layerVis, setLayerVis] = useState<Record<string, boolean>>({
    def: true, str: true, veg: true, ue: true,
    hansen: true, alerts: true, drivers: true, fire: true, anp: true, sar: true,
  })
  const [jobId, setJobId] = useState<string | null>(null)
  const [timelineJobId, setTimelineJobId] = useState<string | null>(null)
  const [results, setResults] = useState<any | null>(null)
  const [isAnalyzing, setIsAnalyzing] = useState(false)
  const [analysisStatus, setAnalysisStatus] = useState<"idle" | "running" | "completed" | "failed">("idle")
  const [showValidation, setShowValidation] = useState(false)
  const [selectedTimelineYear, setSelectedTimelineYear] = useState<number | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const [drawMode, setDrawMode] = useState<DrawMode>("static")
  const [notifyEmail, setNotifyEmail] = useState("")
  const [uploadedPolygons, setUploadedPolygons] = useState<UploadedPolygon[]>([])
  let drawCounter = useRef(0)

  const showToast = (msg: string) => {
    setToast(msg)
    setTimeout(() => setToast(null), 3000)
  }

  // ── Clear AOI ──
  const handleClearAoi = useCallback(() => {
    mapRef.current?.clearAOI()
    setAoi(null)
    setResults(null)
    setJobId(null)
    setTimelineJobId(null)
    setAnalysisStatus("idle")
    setDrawMode("static")
  }, [])

  // ── Start drawing a new polygon ──
  const handleStartDraw = useCallback(() => {
    mapRef.current?.setDrawMode("polygon")
    setDrawMode("polygon")
  }, [])

  // ── Edit AOI (toggle select mode for vertex editing) ──
  const handleEditAoi = useCallback(() => {
    setDrawMode((prev) => {
      const next: DrawMode = prev === "select" ? "static" : "select"
      mapRef.current?.setDrawMode(next)
      return next
    })
  }, [])

  // ── Cancel drawing and return to navigate ──
  const handleCancelDraw = useCallback(() => {
    mapRef.current?.setDrawMode("static")
    setDrawMode("static")
  }, [])

  // ── Engine toggle ──
  const handleToggleEngine = (engine: string) => {
    setEngines((prev) => prev.includes(engine) ? prev.filter((e) => e !== engine) : [...prev, engine])
  }

  // ── Layer visibility (calls into MapView imperative handle) ──
  const handleToggleLayer = (key: string, visible: boolean) => {
    mapRef.current?.toggleLayerVisibility(key as LayerKey, visible)
    setLayerVis((prev) => ({ ...prev, [key]: visible }))
  }

  // ── Polygon area estimate (km²) ──
  const estimateAreaKm2 = (aoiGeom: any): number => {
    const coords = aoiGeom?.coordinates?.[0]
    if (!coords || coords.length < 3) return 0
    // Shoelace formula in degrees, scaled to km²
    let area = 0
    for (let i = 0; i < coords.length - 1; i++) {
      area += coords[i][0] * coords[i + 1][1] - coords[i + 1][0] * coords[i][1]
    }
    area = Math.abs(area) / 2
    const midLat = coords.reduce((s: number, c: number[]) => s + c[1], 0) / coords.length
    const degToKm = 111.32
    return area * degToKm * degToKm * Math.cos((midLat * Math.PI) / 180)
  }

  // ── Analyze ──
  const handleAnalyze = async () => {
    if (!aoi || isAnalyzing) return

    const areaKm2 = estimateAreaKm2(aoi)
    if (areaKm2 > 5000) {
      showToast(`AOI demasiado grande (${areaKm2.toFixed(0)} km²). Máximo: 5000 km²`)
      return
    }
    if (areaKm2 > 150) {
      const n = Math.ceil(Math.sqrt(areaKm2 / 150))
      console.log(`%c[APEX] AOI grande: ${areaKm2.toFixed(0)} km² → se dividirá en ~${n * n} grupos`, "color: #d29922; font-weight: bold")
    }

    const payload = {
      aoi, engines, date_range: ["2022-01-01", "2023-12-31"],
      notify_email: notifyEmail || undefined,
    }
    console.log(`%c[APEX] POST /api/analyze`, "color: #58a6ff; font-weight: bold",
      `\n  engines=${engines.join(",")}`,
      `\n  notify_email=${notifyEmail || "(none)"}`,
      `\n  API=${API_BASE_URL}`,
      `\n  AOI=${JSON.stringify(aoi).slice(0, 150)}`)
    setIsAnalyzing(true)
    setAnalysisStatus("running")
    setResults(null)
    setJobId(null)
    try {
      const res = await fetch(`${API_BASE_URL}/api/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      })
      if (!res.ok) {
        const errBody = await res.text()
        console.error(`%c[APEX] /api/analyze FAILED`, "color: #f85149; font-weight: bold",
          `\n  status=${res.status}`, `\n  body=${errBody}`)
        showToast(`Error del servidor: ${res.status}`)
        setIsAnalyzing(false)
        setAnalysisStatus("failed")
        return
      }
      const data = await res.json()
      console.log(`%c[APEX] Job creado: ${data.job_id}`, "color: #3fb950; font-weight: bold")
      setJobId(data.job_id)
    } catch (e: any) {
      console.error(`%c[APEX] /api/analyze ERROR de red`, "color: #f85149; font-weight: bold",
        `\n  ${e.message || e}`,
        `\n  Verifica que el backend este corriendo en ${API_BASE_URL}`)
      showToast(`No se pudo conectar al backend (${API_BASE_URL})`)
      setIsAnalyzing(false)
      setAnalysisStatus("failed")
    }
  }

  // ── Timeline ──
  const handleTimelineAnalyze = async () => {
    if (!aoi || isAnalyzing) return
    console.log("[APEX] AOI enviado a /api/timeline:", JSON.stringify(aoi).slice(0, 200))
    setIsAnalyzing(true)
    setAnalysisStatus("running")
    setTimelineJobId(null)
    try {
      const res = await fetch(`${API_BASE_URL}/api/timeline`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          aoi,
          start_year: 2018,
          end_year: 2025,
          engines: engines.length > 0 ? engines : ["deforestation", "urban_expansion"],
          season: "dry",
          notify_email: notifyEmail || undefined,
        }),
      })
      const data = await res.json()
      setTimelineJobId(data.job_id)
      setJobId(data.job_id)
    } catch (e: any) {
      console.error("Error enviando timeline:", e)
      setIsAnalyzing(false)
      setAnalysisStatus("failed")
    }
  }

  // ── Job completed ──
  const handleJobCompleted = useCallback(async (completedJobId: string) => {
    try {
      const res = await fetch(`${API_BASE_URL}/api/results/${completedJobId}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setResults(data)
      setAnalysisStatus("completed")
      showToast("Analisis completado — resultados listos")
    } catch (e: any) {
      console.error("Error fetching results:", e)
      setAnalysisStatus("failed")
    } finally {
      setIsAnalyzing(false)
    }
  }, [])

  // ── Render year layers (from timeline panel via sidebar) ──
  const handleRenderYear = useCallback((year: number, data: any) => {
    mapRef.current?.renderYearLayers(year, data)
    setSelectedTimelineYear(year)
  }, [])

  // ── Clear year layers when leaving timeline tab ──
  const handleClearYearLayers = useCallback(() => {
    mapRef.current?.clearYearLayers()
    setSelectedTimelineYear(null)
  }, [])

  // ── Uploaded polygons: sync to map whenever they change ──
  useEffect(() => {
    mapRef.current?.renderUploadedPolygons(uploadedPolygons)
  }, [uploadedPolygons])

  // ── Polygon drawn on map — add to manager ──
  const handlePolygonDrawn = useCallback((geometry: any) => {
    drawCounter.current += 1
    const id = `drawn-${Date.now()}`
    const coords = geometry?.coordinates?.[0] ?? []
    const lngs = coords.map((c: number[]) => c[0])
    const lats = coords.map((c: number[]) => c[1])
    const bbox = coords.length > 0 ? [Math.min(...lngs), Math.min(...lats), Math.max(...lngs), Math.max(...lats)] : null
    const PALETTE = ["#22d3ee", "#f59e0b", "#a78bfa", "#34d399", "#f472b6", "#60a5fa", "#fb923c", "#c084fc"]

    const newPoly: UploadedPolygon = {
      id,
      name: `Dibujo ${drawCounter.current}`,
      filename: "Dibujado en mapa",
      format: "DRAW",
      polygon_count: 1,
      polygons: [geometry],
      feature_collection: {
        type: "FeatureCollection",
        features: [{ type: "Feature", geometry, properties: { index: 0, source_file: "Dibujado en mapa" } }],
      },
      bbox,
      color: PALETTE[drawCounter.current % PALETTE.length],
      visible: true,
    }
    setUploadedPolygons((prev) => [...prev, newPoly])
  }, [])

  // ── Draw mode changed from map (e.g. auto-switch after draw) ──
  const handleDrawModeChange = useCallback((mode: DrawMode) => {
    setDrawMode(mode)
  }, [])

  // ── Use uploaded polygon as AOI ──
  const handleUsePolygonAsAoi = useCallback((geometry: any) => {
    mapRef.current?.setAoiFromGeometry(geometry)
    setAoi(geometry)
    setDrawMode("static")
    showToast("Poligono cargado como AOI")
  }, [])

  // ── Fly to bbox ──
  const handleFlyToBbox = useCallback((bbox: number[]) => {
    mapRef.current?.flyToBbox(bbox)
  }, [])

  return (
    <div className="flex h-screen w-screen overflow-hidden" style={{ backgroundColor: C.bgBase }}>
      {/* ── Sidebar ── */}
      <Sidebar
        aoi={aoi}
        engines={engines}
        onToggleEngine={handleToggleEngine}
        onAnalyze={handleAnalyze}
        onTimelineAnalyze={handleTimelineAnalyze}
        isAnalyzing={isAnalyzing}
        jobId={jobId}
        timelineJobId={timelineJobId}
        results={results}
        onJobCompleted={handleJobCompleted}
        layerVis={layerVis}
        onToggleLayer={handleToggleLayer}
        onRenderYear={handleRenderYear}
        onClearYearLayers={handleClearYearLayers}
        onClearAoi={handleClearAoi}
        onEditAoi={handleEditAoi}
        onStartDraw={handleStartDraw}
        onCancelDraw={handleCancelDraw}
        drawMode={drawMode}
        notifyEmail={notifyEmail}
        onNotifyEmailChange={setNotifyEmail}
        uploadedPolygons={uploadedPolygons}
        onUploadedPolygonsChange={setUploadedPolygons}
        onUsePolygonAsAoi={handleUsePolygonAsAoi}
        onFlyToBbox={handleFlyToBbox}
      />

      {/* ── Main area ── */}
      <div className="flex-1 flex flex-col min-w-0">
        <TopBar
          basemap={basemap}
          onBasemapChange={setBasemap}
          map={mapRef.current?.getMap() ?? null}
          isAnalyzing={isAnalyzing}
          analysisStatus={analysisStatus}
        />

        {/* ── Panel toggle buttons ── */}
        <div className="flex items-center gap-1 px-3 py-1 border-b" style={{ borderColor: C.border, backgroundColor: C.bgPanel }}>
          {user && (
            <span className="text-[10px] mr-auto" style={{ color: C.text2 }}>
              {user.email} ({user.role})
            </span>
          )}
          {(["strategic", "simulator", "impact"] as const).map((p) => (
            <button
              key={p}
              onClick={() => setRightPanel((prev) => prev === p ? "none" : p)}
              className="px-2 py-0.5 rounded text-[10px] font-medium transition-colors"
              style={{
                backgroundColor: rightPanel === p ? "#58a6ff22" : "transparent",
                color: rightPanel === p ? "#58a6ff" : C.text2,
              }}
            >
              {p === "strategic" ? "Estratégico" : p === "simulator" ? "Simulador" : "Impacto"}
            </button>
          ))}
          <button
            onClick={handleLogout}
            className="px-2 py-0.5 rounded text-[10px] font-medium ml-1"
            style={{ color: "#f85149" }}
          >
            Salir
          </button>
        </div>

        {/* ── Map + overlays ── */}
        <div className="flex-1 relative">
          <MapView
            ref={mapRef}
            basemap={basemap}
            results={results}
            onAoiChange={setAoi}
            onPolygonDrawn={handlePolygonDrawn}
            onDrawModeChange={handleDrawModeChange}
            drawMode={drawMode}
          />

          {/* Year badge when viewing timeline */}
          {selectedTimelineYear && (
            <div className="absolute top-3 left-1/2 -translate-x-1/2 z-20 px-3 py-1 rounded-full text-xs font-bold shadow-lg pointer-events-none"
              style={{ backgroundColor: "#d29922", color: "#0d1117" }}>
              Viendo cambios {selectedTimelineYear - 1} &rarr; {selectedTimelineYear}
            </div>
          )}

          {/* Validation toggle button */}
          {results && (
            <button
              onClick={() => setShowValidation((v) => !v)}
              className="absolute top-3 right-3 z-10 px-3 py-1.5 rounded-md text-xs font-medium border transition-colors"
              style={{
                borderColor: C.border,
                color: showValidation ? "#d29922" : C.text2,
                backgroundColor: showValidation ? "#d2992222" : C.bgPanel,
              }}
            >
              {showValidation ? "Ocultar Validacion" : "Validar Detecciones"}
            </button>
          )}

          {/* Validation panel */}
          {results && showValidation && (
            <div
              className="absolute top-12 right-3 z-10 w-72 max-h-[calc(100vh-8rem)] overflow-y-auto rounded-lg p-3"
              style={{ backgroundColor: C.bgPanel + "f2", border: `1px solid ${C.border}`, backdropFilter: "blur(8px)" }}
            >
              <h2 className="text-sm font-bold mb-2" style={{ color: "#d29922" }}>Panel de Validacion</h2>
              <ValidationPanel
                results={results}
                onFlyTo={(lng, lat) => mapRef.current?.flyToCoord(lng, lat)}
                onHighlight={(src, id) => mapRef.current?.highlightFeature(src, id)}
              />
            </div>
          )}

          {/* Legend */}
          {results && <LegendPanel />}
        </div>
      </div>

      {/* ── Right panel (Strategic / Simulator / Impact) ── */}
      {rightPanel !== "none" && (
        <div
          className="w-80 border-l flex-shrink-0 overflow-hidden"
          style={{ borderColor: C.border, backgroundColor: C.bgPanel }}
        >
          {rightPanel === "strategic" && <StrategicPanel token={token} />}
          {rightPanel === "simulator" && <SimulatorPanel token={token} />}
          {rightPanel === "impact" && <ImpactDashboard token={token} />}
        </div>
      )}

      {/* ── Toast ── */}
      {toast && (
        <div className="fixed top-4 left-1/2 -translate-x-1/2 z-50 px-5 py-2 rounded-lg shadow-lg text-sm font-medium animate-pulse"
          style={{ backgroundColor: "#2ea043", color: "#fff" }}>
          {toast}
        </div>
      )}
    </div>
  )
}

export default App