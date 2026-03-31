import { useCallback, useEffect, useRef, useState } from "react"
import { API_BASE_URL } from "../config"

const C = {
  bgPanel: "#161b22",
  bgBase: "#0d1117",
  bgCard: "#21262d",
  border: "#30363d",
  text1: "#e6edf3",
  text2: "#8b949e",
  accent: "#58a6ff",
  yellow: "#d29922",
  green: "#3fb950",
  red: "#f85149",
  orange: "#f0883e",
  purple: "#a371f7",
} as const

const RISK_COLORS: Record<string, string> = {
  CRITICAL: C.red,
  HIGH: C.orange,
  MEDIUM: C.yellow,
  LOW: C.green,
}

const METHOD_LABELS: Record<string, string> = {
  ensemble: "Ensemble (4 capas)",
  trend: "Tendencia lineal",
  ml: "Machine Learning",
  pomdp: "POMDP Rollout",
  convlstm: "ConvLSTM Espaciotemporal",
}

interface YearEntry {
  year: number
  deforestation_ha: number
  urban_expansion_ha: number
  hansen_loss_ha: number
  sar_change_ha: number
  fire_burned_ha: number
  firms_hotspots: number
  firms_frp_mw: number
  alerts_count: number
}

interface Prediction {
  year: number
  deforestation_ha: number
  ci_lower?: number
  ci_upper?: number
  risk: string
  layer_contributions?: Record<string, number>
  p_sin_ilicito?: number
  p_tala?: number
  p_cus_inmobiliario?: number
  p_frontera_agricola?: number
}

interface ForecastResult {
  status: string
  detail?: string
  job_id?: string
  horizon?: number
  method?: string
  years_analyzed?: number
  period?: string
  historical?: YearEntry[]
  predictions?: Prediction[]
  total_deforestation_ha?: number[]
  overall_risk?: string
  trend?: { available: boolean; slope_ha_yr?: number; r_squared?: number; predictions?: Prediction[] }
  ml?: { available: boolean; predictions?: Prediction[] }
  pomdp?: { available: boolean; predictions?: Prediction[] }
  convlstm?: { available: boolean; reason?: string; predictions?: Prediction[] }
  ensemble?: { available: boolean; predictions?: Prediction[] }
  spatial_forecast?: { deforestation?: any; urban_expansion?: any }
}

interface EngineStatus {
  engine: string
  ml_model_trained: boolean
  ml_model_size_kb: number
  convlstm_model_trained?: boolean
  convlstm_model_size_kb?: number
  timeline_jobs: number
  year_records: number
}

interface ForecastPanelProps {
  token: string | null
  aoi: object | null
  timelineJobId?: string | null
  onSpatialForecast?: (data: { deforestation?: any; urban_expansion?: any }) => void
  onClearForecast?: () => void
}

export default function ForecastPanel({ token, aoi: _aoi, timelineJobId, onSpatialForecast, onClearForecast }: ForecastPanelProps) {
  const [horizon, setHorizon] = useState(3)
  const [method, setMethod] = useState("ensemble")
  const [result, setResult] = useState<ForecastResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<EngineStatus | null>(null)
  const [tlStatus, setTlStatus] = useState<"none" | "running" | "completed" | "failed">("none")
  const tlPollRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [training, setTraining] = useState(false)
  const [trainResult, setTrainResult] = useState<any>(null)
  const [trainingConvlstm, setTrainingConvlstm] = useState(false)
  const [trainConvlstmResult, setTrainConvlstmResult] = useState<any>(null)
  const [showOnMap, setShowOnMap] = useState(true)

  const headers: Record<string, string> = { "Content-Type": "application/json" }
  if (token) headers["Authorization"] = `Bearer ${token}`

  // Fetch engine status on mount
  useEffect(() => {
    fetch(`${API_BASE_URL}/api/forecast/status`, { headers })
      .then((r) => r.json())
      .then(setStatus)
      .catch(() => {})
  }, [])

  // Clear old results when timelineJobId changes (new analysis completed)
  useEffect(() => {
    setResult(null)
    setError(null)
    if (onClearForecast) onClearForecast()

    // Poll timeline job status
    if (tlPollRef.current) clearTimeout(tlPollRef.current)
    if (!timelineJobId) { setTlStatus("none"); return }
    setTlStatus("running")

    const pollTl = async () => {
      try {
        const res = await fetch(`${API_BASE_URL}/api/jobs/${timelineJobId}`, { headers })
        if (!res.ok) return
        const data = await res.json()
        if (data.status === "completed") {
          setTlStatus("completed")
          // Refresh engine status after timeline completes
          fetch(`${API_BASE_URL}/api/forecast/status`, { headers })
            .then((r) => r.json())
            .then(setStatus)
            .catch(() => {})
          return
        }
        if (data.status === "failed") { setTlStatus("failed"); return }
        // Still running — poll again
        tlPollRef.current = setTimeout(pollTl, 5000)
      } catch {
        tlPollRef.current = setTimeout(pollTl, 5000)
      }
    }
    pollTl()
    return () => { if (tlPollRef.current) clearTimeout(tlPollRef.current) }
  }, [timelineJobId])

  const runForecast = useCallback(async () => {
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      const body: Record<string, any> = { horizon, method }
      if (timelineJobId) body.job_id = timelineJobId
      const res = await fetch(`${API_BASE_URL}/api/forecast/run`, {
        method: "POST",
        headers,
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }))
        throw new Error(typeof err.detail === "string" ? err.detail : `HTTP ${res.status}`)
      }
      const data = await res.json()
      if (data.status === "no_data") {
        setError(data.detail || "Sin datos de timeline")
      } else {
        setResult(data)
        // Render spatial forecast on map if available
        if (data.spatial_forecast && showOnMap && onSpatialForecast) {
          onSpatialForecast(data.spatial_forecast)
        }
      }
    } catch (e: any) {
      setError(e.message || "Error al ejecutar predicción")
    } finally {
      setLoading(false)
    }
  }, [timelineJobId, horizon, method, showOnMap, onSpatialForecast])

  const handleTrain = async () => {
    setTraining(true)
    setTrainResult(null)
    try {
      const res = await fetch(`${API_BASE_URL}/api/forecast/train`, {
        method: "POST",
        headers,
      })
      const data = await res.json()
      setTrainResult(data)
      // Refresh status
      const s = await fetch(`${API_BASE_URL}/api/forecast/status`, { headers })
      setStatus(await s.json())
    } catch {
      setTrainResult({ status: "error", detail: "Error de red" })
    } finally {
      setTraining(false)
    }
  }

  const handleTrainConvlstm = async () => {
    setTrainingConvlstm(true)
    setTrainConvlstmResult(null)
    try {
      const res = await fetch(`${API_BASE_URL}/api/forecast/train-convlstm`, {
        method: "POST",
        headers,
      })
      const data = await res.json()
      setTrainConvlstmResult(data)
      const s = await fetch(`${API_BASE_URL}/api/forecast/status`, { headers })
      setStatus(await s.json())
    } catch {
      setTrainConvlstmResult({ status: "error", detail: "Error de red" })
    } finally {
      setTrainingConvlstm(false)
    }
  }

  // ── Sparkline mini chart ──
  const Sparkline = ({ values, color }: { values: number[]; color?: string }) => {
    if (!values?.length) return null
    const maxVal = Math.max(...values, 0.1)
    const w = 140
    const h = 28
    const points = values
      .map((v, i) => {
        const x = (i / Math.max(values.length - 1, 1)) * w
        const y = h - (v / maxVal) * h
        return `${x},${y}`
      })
      .join(" ")
    return (
      <svg width={w} height={h} className="inline-block">
        <polyline points={points} fill="none" stroke={color || C.accent} strokeWidth={1.5} />
        {values.map((v, i) => (
          <circle
            key={i}
            cx={(i / Math.max(values.length - 1, 1)) * w}
            cy={h - (v / maxVal) * h}
            r={2}
            fill={color || C.accent}
          />
        ))}
      </svg>
    )
  }

  return (
    <div className="h-full flex flex-col overflow-hidden" style={{ backgroundColor: C.bgPanel }}>
      {/* Header */}
      <div className="px-3 py-2 border-b flex items-center gap-2" style={{ borderColor: C.border }}>
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke={C.purple} strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" />
        </svg>
        <span className="text-sm font-bold" style={{ color: C.text1 }}>Predicción</span>
        <span className="text-[10px] ml-auto px-1.5 py-0.5 rounded" style={{ backgroundColor: C.purple + "22", color: C.purple }}>
          v2 · Timeline
        </span>
      </div>

      {/* Controls */}
      <div className="px-3 py-2 space-y-2 border-b" style={{ borderColor: C.border }}>
        {/* Timeline job indicator */}
        <div className="flex items-center gap-1.5 text-[10px]">
          <span
            className={`w-1.5 h-1.5 rounded-full${tlStatus === "running" ? " animate-pulse" : ""}`}
            style={{ backgroundColor: tlStatus === "completed" ? C.green : tlStatus === "running" ? C.yellow : tlStatus === "failed" ? C.red : C.text2 }}
          />
          <span style={{ color: C.text2 }}>
            {!timelineJobId
              ? "Sin an\u00e1lisis timeline (usa el \u00faltimo disponible)"
              : tlStatus === "running"
              ? `Timeline ${timelineJobId.slice(0, 8)}\u2026 en proceso`
              : tlStatus === "failed"
              ? `Timeline ${timelineJobId.slice(0, 8)}\u2026 fall\u00f3`
              : `Timeline: ${timelineJobId.slice(0, 8)}\u2026`}
          </span>
        </div>

        {/* Horizon selector */}
        <div>
          <label className="text-[10px] font-medium block mb-1" style={{ color: C.text2 }}>
            Horizonte
          </label>
          <div className="flex gap-1">
            {[1, 2, 3, 5].map((h) => (
              <button
                key={h}
                onClick={() => setHorizon(h)}
                className="flex-1 px-1.5 py-1 rounded text-[10px] font-medium transition-colors"
                style={{
                  backgroundColor: horizon === h ? C.accent + "22" : "transparent",
                  color: horizon === h ? C.accent : C.text2,
                  border: `1px solid ${horizon === h ? C.accent + "44" : C.border}`,
                }}
              >
                {h} año{h > 1 ? "s" : ""}
              </button>
            ))}
          </div>
        </div>

        {/* Method selector */}
        <div>
          <label className="text-[10px] font-medium block mb-1" style={{ color: C.text2 }}>
            Método
          </label>
          <select
            value={method}
            onChange={(e) => setMethod(e.target.value)}
            className="w-full text-[11px] px-2 py-1 rounded border"
            style={{ backgroundColor: C.bgCard, borderColor: C.border, color: C.text1 }}
          >
            {Object.entries(METHOD_LABELS).map(([k, v]) => (
              <option key={k} value={k}>{v}</option>
            ))}
          </select>
        </div>

        {/* Run button */}
        <button
          onClick={runForecast}
          disabled={loading || tlStatus === "running"}
          className="w-full py-1.5 rounded text-xs font-medium transition-colors disabled:opacity-40"
          style={{ backgroundColor: C.purple, color: "#fff" }}
        >
          {loading ? "Calculando\u2026" : tlStatus === "running" ? "Esperando timeline\u2026" : "Ejecutar Predicci\u00f3n"}
        </button>

        {/* Show on map toggle */}
        <div className="flex items-center gap-2">
          <button
            onClick={() => {
              const next = !showOnMap
              setShowOnMap(next)
              if (next && result?.spatial_forecast && onSpatialForecast) {
                onSpatialForecast(result.spatial_forecast)
              } else if (!next && onClearForecast) {
                onClearForecast()
              }
            }}
            className="flex items-center gap-1.5 text-[10px] px-2 py-1 rounded transition-colors"
            style={{
              backgroundColor: showOnMap ? C.purple + "22" : "transparent",
              color: showOnMap ? C.purple : C.text2,
              border: `1px solid ${showOnMap ? C.purple + "44" : C.border}`,
            }}
          >
            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 20l-5.447-2.724A1 1 0 013 16.382V5.618a1 1 0 011.447-.894L9 7m0 13l6-3m-6 3V7m6 10l4.553 2.276A1 1 0 0021 18.382V7.618a1 1 0 00-.553-.894L15 4m0 13V4m0 0L9 7" />
            </svg>
            Mostrar en mapa
          </button>
        </div>
      </div>

      {/* Scrollable content */}
      <div className="flex-1 overflow-y-auto px-3 py-2 space-y-3" style={{ scrollbarWidth: "thin" }}>
        {/* Error */}
        {error && (
          <div className="text-[11px] px-2 py-1.5 rounded" style={{ backgroundColor: C.red + "22", color: C.red }}>
            {error}
          </div>
        )}

        {/* Engine status */}
        {status && (
          <div className="rounded p-2 space-y-1" style={{ backgroundColor: C.bgCard, border: `1px solid ${C.border}` }}>
            <div className="text-[10px] font-semibold uppercase" style={{ color: C.text2 }}>Estado del motor</div>
            <div className="grid grid-cols-2 gap-1 text-[10px]">
              <span style={{ color: C.text2 }}>Jobs timeline:</span>
              <span style={{ color: C.text1 }}>{status.timeline_jobs}</span>
              <span style={{ color: C.text2 }}>Registros/año:</span>
              <span style={{ color: C.text1 }}>{status.year_records}</span>
              <span style={{ color: C.text2 }}>Modelo ML:</span>
              <span style={{ color: status.ml_model_trained ? C.green : C.yellow }}>
                {status.ml_model_trained ? `Entrenado (${status.ml_model_size_kb} KB)` : "Sin entrenar"}
              </span>
              <span style={{ color: C.text2 }}>ConvLSTM:</span>
              <span style={{ color: status.convlstm_model_trained ? C.green : C.yellow }}>
                {status.convlstm_model_trained ? `Entrenado (${status.convlstm_model_size_kb} KB)` : "Sin entrenar"}
              </span>
            </div>
            <button
              onClick={handleTrain}
              disabled={training || status.year_records < 3}
              className="w-full mt-1 py-1 rounded text-[10px] font-medium transition-colors"
              style={{ backgroundColor: C.bgBase, border: `1px solid ${C.border}`, color: C.text2 }}
            >
              {training ? "Entrenando…" : "Entrenar modelo ML"}
            </button>
            {trainResult && (
              <div className="text-[10px] mt-1" style={{ color: trainResult.status === "ok" ? C.green : C.red }}>
                {trainResult.status === "ok"
                  ? `OK — MAE: ${trainResult.mae}, R²: ${trainResult.r2}, ${trainResult.samples} muestras`
                  : `Error: ${trainResult.detail}`}
              </div>
            )}
            <button
              onClick={handleTrainConvlstm}
              disabled={trainingConvlstm || status.year_records < 5}
              className="w-full mt-1 py-1 rounded text-[10px] font-medium transition-colors"
              style={{ backgroundColor: C.bgBase, border: `1px solid ${C.border}`, color: C.text2 }}
            >
              {trainingConvlstm ? "Entrenando ConvLSTM…" : "Entrenar ConvLSTM"}
            </button>
            {trainConvlstmResult && (
              <div className="text-[10px] mt-1" style={{ color: trainConvlstmResult.status === "ok" ? C.green : C.red }}>
                {trainConvlstmResult.status === "ok"
                  ? `OK — ${trainConvlstmResult.epochs || ""} epochs, loss: ${trainConvlstmResult.final_loss?.toFixed(4) ?? "—"}`
                  : `Error: ${trainConvlstmResult.detail}`}
              </div>
            )}
          </div>
        )}

        {/* Results */}
        {result && result.status === "ok" && (
          <>
            {/* Summary bar */}
            <div className="rounded p-2" style={{ backgroundColor: C.bgCard, border: `1px solid ${C.border}` }}>
              <div className="flex items-center justify-between mb-1">
                <span className="text-[10px] font-semibold uppercase" style={{ color: C.text2 }}>
                  Análisis: {result.period}
                </span>
                <span
                  className="text-[10px] px-1.5 py-0.5 rounded font-medium"
                  style={{
                    backgroundColor: RISK_COLORS[result.overall_risk || "LOW"] + "22",
                    color: RISK_COLORS[result.overall_risk || "LOW"],
                  }}
                >
                  Riesgo: {result.overall_risk}
                </span>
              </div>
              <div className="text-[10px]" style={{ color: C.text2 }}>
                {result.years_analyzed} años analizados · método: {METHOD_LABELS[result.method || "ensemble"]}
              </div>
            </div>

            {/* Historical data mini chart */}
            {result.historical && result.historical.length > 0 && (
              <div className="rounded p-2" style={{ backgroundColor: C.bgCard, border: `1px solid ${C.border}` }}>
                <div className="text-[10px] font-semibold uppercase mb-1.5" style={{ color: C.text2 }}>
                  Datos históricos (ha)
                </div>
                <div className="space-y-1">
                  {result.historical.map((h) => {
                    const maxHa = Math.max(...result.historical!.map((x) => x.deforestation_ha), 1)
                    return (
                      <div key={h.year} className="flex items-center gap-2">
                        <span className="text-[10px] w-10 font-mono" style={{ color: C.text2 }}>{h.year}</span>
                        <div className="flex-1 h-2.5 rounded-full overflow-hidden" style={{ backgroundColor: C.bgBase }}>
                          <div
                            className="h-full rounded-full"
                            style={{
                              width: `${Math.max((h.deforestation_ha / maxHa) * 100, 1)}%`,
                              backgroundColor: h.deforestation_ha > 10 ? C.red : h.deforestation_ha > 5 ? C.orange : h.deforestation_ha > 1 ? C.yellow : C.green,
                            }}
                          />
                        </div>
                        <span className="text-[10px] w-14 text-right font-mono" style={{ color: C.text1 }}>
                          {h.deforestation_ha.toFixed(1)}
                        </span>
                      </div>
                    )
                  })}
                </div>

                {/* Multi-metric sparklines */}
                <div className="mt-2 pt-1.5 border-t grid grid-cols-2 gap-2" style={{ borderColor: C.border }}>
                  <div>
                    <div className="text-[9px]" style={{ color: C.text2 }}>Deforestación</div>
                    <Sparkline values={result.historical.map((h) => h.deforestation_ha)} color={C.red} />
                  </div>
                  <div>
                    <div className="text-[9px]" style={{ color: C.text2 }}>Exp. urbana</div>
                    <Sparkline values={result.historical.map((h) => h.urban_expansion_ha)} color={C.orange} />
                  </div>
                  <div>
                    <div className="text-[9px]" style={{ color: C.text2 }}>FIRMS hotspots</div>
                    <Sparkline values={result.historical.map((h) => h.firms_hotspots)} color={C.yellow} />
                  </div>
                  <div>
                    <div className="text-[9px]" style={{ color: C.text2 }}>Alertas</div>
                    <Sparkline values={result.historical.map((h) => h.alerts_count)} color={C.purple} />
                  </div>
                </div>
              </div>
            )}

            {/* Projected deforestation */}
            {result.predictions && result.predictions.length > 0 && (
              <div className="rounded p-2" style={{ backgroundColor: C.bgCard, border: `1px solid ${C.border}` }}>
                <div className="text-[10px] font-semibold uppercase mb-1.5" style={{ color: C.text2 }}>
                  Deforestación proyectada
                </div>
                <div className="space-y-1.5">
                  {result.predictions.map((p) => {
                    const maxHa = Math.max(...result.predictions!.map((x) => x.ci_upper || x.deforestation_ha), 1)
                    return (
                      <div key={p.year}>
                        <div className="flex items-center gap-2">
                          <span className="text-[10px] w-10 font-mono" style={{ color: C.text2 }}>{p.year}</span>
                          <div className="flex-1 h-3 rounded-full overflow-hidden relative" style={{ backgroundColor: C.bgBase }}>
                            {p.ci_upper != null && (
                              <div
                                className="absolute h-full rounded-full opacity-30"
                                style={{
                                  left: `${((p.ci_lower || 0) / maxHa) * 100}%`,
                                  width: `${(((p.ci_upper || 0) - (p.ci_lower || 0)) / maxHa) * 100}%`,
                                  backgroundColor: RISK_COLORS[p.risk] || C.accent,
                                }}
                              />
                            )}
                            <div
                              className="h-full rounded-full relative z-10"
                              style={{
                                width: `${Math.max((p.deforestation_ha / maxHa) * 100, 2)}%`,
                                backgroundColor: RISK_COLORS[p.risk] || C.accent,
                              }}
                            />
                          </div>
                          <span className="text-[10px] w-14 text-right font-mono" style={{ color: RISK_COLORS[p.risk] }}>
                            {p.deforestation_ha.toFixed(1)} ha
                          </span>
                          <span
                            className="text-[9px] px-1 py-0.5 rounded"
                            style={{ backgroundColor: RISK_COLORS[p.risk] + "22", color: RISK_COLORS[p.risk] }}
                          >
                            {p.risk}
                          </span>
                        </div>
                        {p.ci_lower != null && (
                          <div className="text-[9px] ml-12" style={{ color: C.text2 }}>
                            IC: [{p.ci_lower.toFixed(1)} – {(p.ci_upper || 0).toFixed(1)}] ha
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>
            )}

            {/* Spatial forecast info */}
            {result.spatial_forecast && (
              <div className="rounded p-2" style={{ backgroundColor: C.bgCard, border: `1px solid ${C.purple}44` }}>
                <div className="flex items-center gap-1.5 mb-1.5">
                  <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke={C.purple} strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M9 20l-5.447-2.724A1 1 0 013 16.382V5.618a1 1 0 011.447-.894L9 7m0 13l6-3m-6 3V7m6 10l4.553 2.276A1 1 0 0021 18.382V7.618a1 1 0 00-.553-.894L15 4m0 13V4m0 0L9 7" />
                  </svg>
                  <span className="text-[10px] font-semibold uppercase" style={{ color: C.purple }}>
                    Expansión espacial proyectada
                  </span>
                </div>
                <div className="space-y-1 text-[10px]">
                  {result.spatial_forecast.deforestation?.features?.length > 0 && (
                    <div className="flex items-center gap-1.5">
                      <span className="w-4 h-0.5" style={{ backgroundColor: "#a855f7", borderTop: "2px dashed #a855f7" }} />
                      <span style={{ color: C.text1 }}>
                        Deforestación: {result.spatial_forecast.deforestation.features.length} zona(s) proyectada(s)
                      </span>
                    </div>
                  )}
                  {result.spatial_forecast.urban_expansion?.features?.length > 0 && (
                    <div className="flex items-center gap-1.5">
                      <span className="w-4 h-0.5" style={{ backgroundColor: "#06b6d4", borderTop: "2px dashed #06b6d4" }} />
                      <span style={{ color: C.text1 }}>
                        Exp. urbana: {result.spatial_forecast.urban_expansion.features.length} zona(s) proyectada(s)
                      </span>
                    </div>
                  )}
                  <p className="text-[9px] mt-1" style={{ color: C.text2 }}>
                    {showOnMap ? "Zonas de expansión mostradas en el mapa con líneas punteadas." : "Activa \"Mostrar en mapa\" para visualizar las zonas."}
                  </p>
                </div>
              </div>
            )}

            {/* Layer details */}
            {method === "ensemble" && (
              <div className="rounded p-2" style={{ backgroundColor: C.bgCard, border: `1px solid ${C.border}` }}>
                <div className="text-[10px] font-semibold uppercase mb-1.5" style={{ color: C.text2 }}>
                  Capas del ensemble
                </div>
                {(["trend", "ml", "pomdp", "convlstm"] as const).map((layer) => {
                  const layerData = result[layer]
                  if (!layerData) return null
                  return (
                    <div key={layer} className="flex items-center gap-1.5 text-[10px] py-0.5">
                      <span
                        className="w-1.5 h-1.5 rounded-full"
                        style={{ backgroundColor: layerData.available ? C.green : C.red }}
                      />
                      <span style={{ color: layerData.available ? C.text1 : C.text2 }}>
                        {layer === "trend" ? "Tendencia" : layer === "ml" ? "ML (Random Forest)" : layer === "pomdp" ? "POMDP" : "ConvLSTM"}
                      </span>
                      {layerData.available && layerData.predictions?.[0] && (
                        <span className="ml-auto font-mono" style={{ color: C.text2 }}>
                          {layerData.predictions[0].deforestation_ha.toFixed(2)} ha/yr
                        </span>
                      )}
                      {!layerData.available && (
                        <span className="ml-auto" style={{ color: C.text2, fontSize: "9px" }}>
                          {(layerData as any).reason || "No disponible"}
                        </span>
                      )}
                    </div>
                  )
                })}
                {result.trend?.available && result.trend.slope_ha_yr != null && (
                  <div className="mt-1 pt-1 border-t text-[9px]" style={{ borderColor: C.border, color: C.text2 }}>
                    Tendencia: {result.trend.slope_ha_yr > 0 ? "+" : ""}{result.trend.slope_ha_yr.toFixed(3)} ha/año
                    · R² = {result.trend.r_squared?.toFixed(3)}
                  </div>
                )}
              </div>
            )}

            {/* POMDP probabilities */}
            {result.pomdp?.available && result.pomdp.predictions && (
              <div className="rounded p-2" style={{ backgroundColor: C.bgCard, border: `1px solid ${C.border}` }}>
                <div className="text-[10px] font-semibold uppercase mb-1.5" style={{ color: C.text2 }}>
                  Probabilidad de ilícitos (POMDP)
                </div>
                <div className="space-y-1">
                  {result.pomdp.predictions.map((p) => (
                    <div key={p.year} className="text-[10px]">
                      <span className="font-mono w-10 inline-block" style={{ color: C.text2 }}>{p.year}</span>
                      <span className="ml-1" style={{ color: C.green }}>
                        Sin ilícito: {((p.p_sin_ilicito || 0) * 100).toFixed(0)}%
                      </span>
                      <span className="ml-2" style={{ color: C.red }}>
                        Tala: {((p.p_tala || 0) * 100).toFixed(0)}%
                      </span>
                      <span className="ml-2" style={{ color: C.orange }}>
                        CUS: {((p.p_cus_inmobiliario || 0) * 100).toFixed(0)}%
                      </span>
                      <span className="ml-2" style={{ color: C.yellow }}>
                        Agri: {((p.p_frontera_agricola || 0) * 100).toFixed(0)}%
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}

        {/* Empty state */}
        {!result && !loading && !error && (
          <div className="text-center py-6">
            <svg className="w-10 h-10 mx-auto mb-2 opacity-30" fill="none" viewBox="0 0 24 24" stroke={C.text2} strokeWidth={1}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" />
            </svg>
            <p className="text-[11px]" style={{ color: C.text2 }}>
              {timelineJobId
                ? "Selecciona horizonte y método, luego ejecuta la predicción."
                : "Ejecuta primero un análisis Timeline, luego genera la predicción aquí."}
            </p>
          </div>
        )}
      </div>
    </div>
  )
}
