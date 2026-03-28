import { useState, useEffect } from "react"
import {
  ComposedChart, Area, Line, XAxis, YAxis,
  Tooltip, ResponsiveContainer,
} from "recharts"
import { API_BASE_URL } from "../config"

const C = {
  bgCard: "#21262d",
  border: "#30363d",
  text1: "#e6edf3",
  text2: "#8b949e",
} as const

const VEG_KEYS = ["bosque_denso", "matorral", "cultivos", "urbano", "suelo", "pastizal"] as const
const VEG_COLORS: Record<string, string> = {
  bosque_denso: "#166534", matorral: "#dfc35a", cultivos: "#e49635",
  urbano: "#6b21a8", suelo: "#92400e", pastizal: "#86efac",
}
const VEG_LABELS: Record<string, string> = {
  bosque_denso: "Bosque denso", matorral: "Matorral", cultivos: "Cultivos",
  urbano: "Urbano", suelo: "Suelo", pastizal: "Pastizal",
  deforestation_ha: "Deforestacion (ha)", urban_expansion_ha: "Expansion (ha)",
  burned_ha: "Incendios (ha)", hansen_ha: "Hansen (ha)",
  firms_hotspots: "FIRMS hotspots", sar_ha: "SAR cambio (ha)",
  alerts_count: "Alertas",
}

interface ChartRow {
  year: number
  bosque_denso: number; matorral: number; cultivos: number
  urbano: number; suelo: number; pastizal: number
  deforestation_ha: number; urban_expansion_ha: number
  burned_ha: number; hansen_ha: number; firms_hotspots: number
  sar_ha: number; alerts_count: number
}

interface Anomaly {
  year: number; engine: string; area_ha: number; z_score: number
  mean_ha: number; severity: string; color: string; message: string
}

interface Cumulative {
  total_deforestation_ha: number; total_urban_expansion_ha: number
  total_burned_ha: number; total_firms_hotspots: number
  total_hansen_loss_ha: number; total_sar_change_ha: number
  total_alerts: number
  bosque_denso_change_pct: number; urbano_change_pct: number
  years_analyzed: number; period: string
  engines_used?: string[]
}

export default function TimelinePanel({
  jobId,
  onYearSelect,
}: {
  jobId: string
  onYearSelect: (year: number, layers: any) => void
}) {
  const [data, setData] = useState<ChartRow[]>([])
  const [selectedYear, setSelectedYear] = useState<number | null>(null)
  const [yearLayers, setYearLayers] = useState<Record<number, any>>({})
  const [anomalies, setAnomalies] = useState<Anomaly[]>([])
  const [cumulative, setCumulative] = useState<Cumulative | null>(null)
  const [status, setStatus] = useState<string>("polling")
  const [progress, setProgress] = useState(0)
  const [currentStep, setCurrentStep] = useState("")
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!jobId) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout>

    const poll = async () => {
      try {
        // 1. Check job status
        const statusRes = await fetch(`${API_BASE_URL}/api/jobs/${jobId}`)
        if (cancelled) return
        if (!statusRes.ok) {
          setError(`Error consultando job: ${statusRes.status}`)
          return
        }
        const statusData = await statusRes.json()
        setProgress(statusData.progress ?? 0)
        setCurrentStep(statusData.current_step ?? "")

        if (statusData.status === "failed") {
          setStatus("failed")
          setError(statusData.current_step || "Pipeline falló")
          return
        }

        if (statusData.status !== "completed") {
          setStatus("polling")
          timer = setTimeout(poll, 4000)
          return
        }

        // 2. Job completed — fetch results
        setStatus("loading")
        const res = await fetch(`${API_BASE_URL}/api/results/${jobId}`)
        if (cancelled) return
        if (!res.ok) {
          setError(`Error obteniendo resultados: ${res.status}`)
          return
        }
        const resData = await res.json()
        const summaryGeo = resData.layers?.timeline_summary?.geojson ?? {}
        const timeline = summaryGeo.timeline ?? {}
        const chartData: ChartRow[] = Object.entries(timeline).map(
          ([yr, d]: [string, any]) => ({
            year: parseInt(yr),
            bosque_denso: d.vegetation?.stats?.classes?.bosque_denso ?? 0,
            matorral: d.vegetation?.stats?.classes?.matorral ?? 0,
            cultivos: d.vegetation?.stats?.classes?.cultivos ?? 0,
            urbano: d.vegetation?.stats?.classes?.urbano ?? 0,
            suelo: d.vegetation?.stats?.classes?.suelo ?? 0,
            pastizal: d.vegetation?.stats?.classes?.pastizal ?? 0,
            deforestation_ha: d.deforestation?.stats?.area_ha ?? 0,
            urban_expansion_ha: d.urban_expansion?.stats?.area_ha ?? 0,
            burned_ha: d.fire?.stats?.total_burned_ha ?? 0,
            hansen_ha: d.hansen?.stats?.loss_ha ?? 0,
            firms_hotspots: d.firms_hotspots?.stats?.hotspot_count ?? 0,
            sar_ha: d.sar?.stats?.total_change_ha ?? 0,
            alerts_count: d.alerts?.stats?.total_alerts ?? 0,
          })
        )
        chartData.sort((a, b) => a.year - b.year)
        setData(chartData)
        setYearLayers(timeline)
        setAnomalies(summaryGeo.anomalies ?? [])
        setCumulative(summaryGeo.cumulative ?? null)
        setStatus("done")
      } catch (e: any) {
        if (!cancelled) setError(e.message ?? "Error de red")
      }
    }

    poll()
    return () => { cancelled = true; clearTimeout(timer) }
  }, [jobId])

  const handleYearClick = (year: number) => {
    setSelectedYear(year)
    onYearSelect(year, yearLayers[year])
  }

  if (error) {
    return (
      <div className="p-3 rounded border" style={{ backgroundColor: "#21262d", borderColor: "#da3633" }}>
        <p className="text-xs text-red-400 font-bold mb-1">Error en Timeline</p>
        <p className="text-[10px] text-[#8b949e]">{error}</p>
      </div>
    )
  }

  if (status === "polling" || status === "loading") {
    return (
      <div className="p-3 rounded border" style={{ backgroundColor: "#21262d", borderColor: "#30363d" }}>
        <p className="text-xs font-bold mb-2" style={{ color: "#d29922" }}>
          {status === "polling" ? "Procesando Timeline..." : "Cargando resultados..."}
        </p>
        <div className="w-full h-1.5 rounded bg-[#30363d] mb-2">
          <div
            className="h-full rounded transition-all duration-500"
            style={{ width: `${progress}%`, backgroundColor: "#d29922" }}
          />
        </div>
        <p className="text-[10px]" style={{ color: "#8b949e" }}>{currentStep || "Iniciando..."}</p>
        <p className="text-[10px]" style={{ color: "#8b949e" }}>{progress}%</p>
      </div>
    )
  }

  if (data.length === 0) return null

  const sel = selectedYear ? yearLayers[selectedYear] : null

  /* Custom dot: highlight anomaly years */
  const CustomDot = (props: any) => {
    const { cx, cy, payload } = props
    const isAnomaly = anomalies.some(
      (a) => a.year === payload?.year && a.engine === "deforestation"
    )
    if (isAnomaly) {
      return <circle cx={cx} cy={cy} r={5} fill="#fbbf24" stroke="#ef4444" strokeWidth={2} />
    }
    return <circle cx={cx} cy={cy} r={3} fill="#ef4444" />
  }

  return (
    <div>
      <p className="text-xs font-bold mb-2" style={{ color: "#d29922" }}>Serie Temporal — Vegetacion + Cambios</p>

      {/* ── Cumulative summary grid ── */}
      {cumulative && (
        <div className="grid grid-cols-2 gap-1 mb-2">
          <div className="bg-[#21262d] rounded p-2 border border-[#30363d]">
            <p className="text-[9px] text-[#8b949e] uppercase tracking-wide">Total deforestado</p>
            <p className="text-base font-bold text-red-400">{cumulative.total_deforestation_ha} ha</p>
            <p className="text-[9px] text-[#8b949e]">{cumulative.period}</p>
          </div>
          <div className="bg-[#21262d] rounded p-2 border border-[#30363d]">
            <p className="text-[9px] text-[#8b949e] uppercase tracking-wide">Expansion urbana total</p>
            <p className="text-base font-bold text-orange-400">{cumulative.total_urban_expansion_ha} ha</p>
            <p className="text-[9px] text-[#8b949e]">{cumulative.years_analyzed} años</p>
          </div>
          {(cumulative.total_burned_ha ?? 0) > 0 && (
            <div className="bg-[#21262d] rounded p-2 border border-[#30363d]">
              <p className="text-[9px] text-[#8b949e] uppercase tracking-wide">Total quemado</p>
              <p className="text-base font-bold text-orange-500">{cumulative.total_burned_ha} ha</p>
            </div>
          )}
          {(cumulative.total_hansen_loss_ha ?? 0) > 0 && (
            <div className="bg-[#21262d] rounded p-2 border border-[#30363d]">
              <p className="text-[9px] text-[#8b949e] uppercase tracking-wide">Hansen pérdida</p>
              <p className="text-base font-bold text-yellow-400">{cumulative.total_hansen_loss_ha} ha</p>
            </div>
          )}
          {(cumulative.total_firms_hotspots ?? 0) > 0 && (
            <div className="bg-[#21262d] rounded p-2 border border-[#30363d]">
              <p className="text-[9px] text-[#8b949e] uppercase tracking-wide">FIRMS hotspots</p>
              <p className="text-base font-bold" style={{ color: "#ff3b30" }}>{cumulative.total_firms_hotspots}</p>
            </div>
          )}
          {(cumulative.total_sar_change_ha ?? 0) > 0 && (
            <div className="bg-[#21262d] rounded p-2 border border-[#30363d]">
              <p className="text-[9px] text-[#8b949e] uppercase tracking-wide">SAR cambio</p>
              <p className="text-base font-bold text-cyan-400">{cumulative.total_sar_change_ha} ha</p>
            </div>
          )}
          {(cumulative.total_alerts ?? 0) > 0 && (
            <div className="bg-[#21262d] rounded p-2 border border-[#30363d]">
              <p className="text-[9px] text-[#8b949e] uppercase tracking-wide">Total alertas</p>
              <p className="text-base font-bold" style={{ color: "#dc2626" }}>{cumulative.total_alerts}</p>
            </div>
          )}
          <div className="bg-[#21262d] rounded p-2 border border-[#30363d]">
            <p className="text-[9px] text-[#8b949e]">Cambio bosque denso</p>
            <p className={`text-sm font-bold ${cumulative.bosque_denso_change_pct < 0 ? "text-red-400" : "text-green-400"}`}>
              {cumulative.bosque_denso_change_pct > 0 ? "+" : ""}{cumulative.bosque_denso_change_pct}%
            </p>
          </div>
          <div className="bg-[#21262d] rounded p-2 border border-[#30363d]">
            <p className="text-[9px] text-[#8b949e]">Cambio zona urbana</p>
            <p className="text-sm font-bold text-purple-400">
              +{cumulative.urbano_change_pct}%
            </p>
          </div>
        </div>
      )}

      <ResponsiveContainer width="100%" height={180}>
        <ComposedChart
          data={data}
          onClick={(e: any) => e?.activeLabel && handleYearClick(Number(e.activeLabel))}
        >
          <XAxis dataKey="year" tick={{ fontSize: 9, fill: C.text2 }} />
          <YAxis yAxisId={0} tick={{ fontSize: 9, fill: C.text2 }} unit="%" width={30} />
          <YAxis yAxisId={1} orientation="right" tick={{ fontSize: 9, fill: C.text2 }} unit="ha" width={35} />
          <Tooltip
            contentStyle={{ background: "#161b22", border: `1px solid ${C.border}`, borderRadius: "8px", fontSize: 10 }}
            itemStyle={{ padding: 0 }}
            formatter={(val: any, name) => [
              typeof val === "number" ? val.toFixed(1) : val,
              VEG_LABELS[String(name)] ?? name,
            ]}
          />
          {/* Stacked vegetation areas */}
          {VEG_KEYS.map((k) => (
            <Area key={k} type="monotone" dataKey={k} stackId="veg" yAxisId={0}
              fill={VEG_COLORS[k]} stroke="none" fillOpacity={0.8} />
          ))}
          {/* Change lines on top */}
          <Line type="monotone" dataKey="deforestation_ha" stroke="#ef4444" strokeWidth={2}
            dot={<CustomDot />} yAxisId={1} />
          <Line type="monotone" dataKey="urban_expansion_ha" stroke="#f97316" strokeWidth={2}
            dot={{ r: 3, fill: "#f97316" }} yAxisId={1} />
          <Line type="monotone" dataKey="burned_ha" stroke="#fb923c" strokeWidth={1.5}
            dot={{ r: 2, fill: "#fb923c" }} yAxisId={1} strokeDasharray="4 2" />
          <Line type="monotone" dataKey="hansen_ha" stroke="#facc15" strokeWidth={1.5}
            dot={{ r: 2, fill: "#facc15" }} yAxisId={1} strokeDasharray="4 2" />
          <Line type="monotone" dataKey="sar_ha" stroke="#06b6d4" strokeWidth={1.5}
            dot={{ r: 2, fill: "#06b6d4" }} yAxisId={1} strokeDasharray="2 2" />
        </ComposedChart>
      </ResponsiveContainer>

      {/* Veg legend mini */}
      <div className="flex flex-wrap gap-x-3 gap-y-0.5 mt-1 mb-2">
        {VEG_KEYS.map((k) => (
          <div key={k} className="flex items-center gap-1">
            <span className="w-2 h-2 rounded-sm" style={{ backgroundColor: VEG_COLORS[k] }} />
            <span className="text-[9px]" style={{ color: C.text2 }}>{VEG_LABELS[k]}</span>
          </div>
        ))}
        {[
          { color: "#ef4444", label: "Deforest." },
          { color: "#f97316", label: "Exp. urb." },
          { color: "#fb923c", label: "Incendios" },
          { color: "#facc15", label: "Hansen" },
          { color: "#06b6d4", label: "SAR" },
        ].map(({ color, label }) => (
          <div key={label} className="flex items-center gap-1">
            <span className="w-3 h-0.5 rounded" style={{ backgroundColor: color }} />
            <span className="text-[9px]" style={{ color: C.text2 }}>{label}</span>
          </div>
        ))}
      </div>

      {/* Year selector buttons */}
      <div>
        <p className="text-xs mb-1" style={{ color: C.text2 }}>
          Ver año: <span className="font-bold" style={{ color: C.text1 }}>{selectedYear ?? "\u2014"}</span>
        </p>
        <div className="flex gap-1 flex-wrap">
          {data.map((d) => (
            <button
              key={d.year}
              onClick={() => handleYearClick(d.year)}
              className="text-xs px-2 py-0.5 rounded transition-colors"
              style={selectedYear === d.year
                ? { backgroundColor: "#d29922", color: "#0d1117", fontWeight: 700 }
                : { backgroundColor: C.bgCard, color: C.text2, border: `1px solid ${C.border}` }
              }
            >
              {d.year}
            </button>
          ))}
        </div>
      </div>

      {/* Mini stats for selected year */}
      {sel && selectedYear && (
        <div className="mt-2 grid grid-cols-2 gap-1">
          {sel.deforestation && (
            <div className="rounded p-2" style={{ backgroundColor: C.bgCard, border: `1px solid ${C.border}` }}>
              <p className="text-[10px]" style={{ color: C.text2 }}>Deforestacion</p>
              <p className="text-sm font-bold" style={{ color: "#f85149" }}>
                {sel.deforestation?.stats?.area_ha ?? 0} ha
              </p>
            </div>
          )}
          {sel.urban_expansion && (
            <div className="rounded p-2" style={{ backgroundColor: C.bgCard, border: `1px solid ${C.border}` }}>
              <p className="text-[10px]" style={{ color: C.text2 }}>Exp. urbana</p>
              <p className="text-sm font-bold" style={{ color: "#f0883e" }}>
                {sel.urban_expansion?.stats?.area_ha ?? 0} ha
              </p>
            </div>
          )}
          {sel.fire && (
            <div className="rounded p-2" style={{ backgroundColor: C.bgCard, border: `1px solid ${C.border}` }}>
              <p className="text-[10px]" style={{ color: C.text2 }}>Incendios MODIS</p>
              <p className="text-sm font-bold" style={{ color: "#fb923c" }}>
                {sel.fire?.stats?.total_burned_ha ?? 0} ha
              </p>
            </div>
          )}
          {sel.hansen && (
            <div className="rounded p-2" style={{ backgroundColor: C.bgCard, border: `1px solid ${C.border}` }}>
              <p className="text-[10px]" style={{ color: C.text2 }}>Hansen</p>
              <p className="text-sm font-bold" style={{ color: "#facc15" }}>
                {sel.hansen?.stats?.loss_ha ?? 0} ha
              </p>
            </div>
          )}
          {sel.alerts && (
            <div className="rounded p-2" style={{ backgroundColor: C.bgCard, border: `1px solid ${C.border}` }}>
              <p className="text-[10px]" style={{ color: C.text2 }}>Alertas GLAD/RADD</p>
              <p className="text-sm font-bold" style={{ color: "#dc2626" }}>
                {sel.alerts?.stats?.total_alerts ?? 0}
              </p>
            </div>
          )}
          {sel.firms_hotspots && (
            <div className="rounded p-2" style={{ backgroundColor: C.bgCard, border: `1px solid ${C.border}` }}>
              <p className="text-[10px]" style={{ color: C.text2 }}>FIRMS hotspots</p>
              <p className="text-sm font-bold" style={{ color: "#ff3b30" }}>
                {sel.firms_hotspots?.stats?.hotspot_count ?? 0}
              </p>
            </div>
          )}
          {sel.sar && (
            <div className="rounded p-2" style={{ backgroundColor: C.bgCard, border: `1px solid ${C.border}` }}>
              <p className="text-[10px]" style={{ color: C.text2 }}>SAR cambio</p>
              <p className="text-sm font-bold" style={{ color: "#06b6d4" }}>
                {sel.sar?.stats?.total_change_ha ?? 0} ha
              </p>
            </div>
          )}
          {sel.structures && (
            <div className="rounded p-2" style={{ backgroundColor: C.bgCard, border: `1px solid ${C.border}` }}>
              <p className="text-[10px]" style={{ color: C.text2 }}>Estructuras</p>
              <p className="text-sm font-bold" style={{ color: "#58a6ff" }}>
                {sel.structures?.stats?.count ?? sel.structures?.geojson?.features?.length ?? 0}
              </p>
            </div>
          )}
          {sel.drivers && (
            <div className="rounded p-2" style={{ backgroundColor: C.bgCard, border: `1px solid ${C.border}` }}>
              <p className="text-[10px]" style={{ color: C.text2 }}>Drivers WRI</p>
              <p className="text-sm font-bold" style={{ color: "#8b5cf6" }}>
                {sel.drivers?.stats?.n_features ?? 0}
              </p>
            </div>
          )}
          <div className="rounded p-2 col-span-2" style={{ backgroundColor: C.bgCard, border: `1px solid ${C.border}` }}>
            <p className="text-[10px]" style={{ color: C.text2 }}>Bosque denso</p>
            <p className="text-sm font-bold" style={{ color: "#2ea043" }}>
              {sel.vegetation?.stats?.classes?.bosque_denso ?? 0}%
            </p>
          </div>
        </div>
      )}

      {/* ── Anomaly alerts ── */}
      {anomalies.length > 0 && (
        <div className="mt-2 space-y-1">
          <p className="text-[9px] text-yellow-400 font-bold uppercase tracking-wide">Eventos anomalos detectados</p>
          {anomalies.map((a) => (
            <div
              key={`${a.year}-${a.engine}`}
              className="flex items-center gap-2 bg-yellow-950/40 border border-yellow-800/40 rounded px-2 py-1 cursor-pointer hover:bg-yellow-900/40"
              onClick={() => handleYearClick(a.year)}
            >
              <div className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: a.color }} />
              <p className="text-[10px] text-yellow-200 flex-1">{a.message}</p>
              <span
                className={`text-[9px] px-1.5 py-0.5 rounded font-bold ${
                  a.severity === "alta" ? "bg-red-900 text-red-200" : "bg-yellow-900 text-yellow-200"
                }`}
              >
                {a.severity}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* ── Export report buttons ── */}
      <div className="mt-2 flex gap-1.5">
        <button
          onClick={() => window.open(`${API_BASE_URL}/api/export/${jobId}/report?format=pdf`)}
          className="flex-1 text-xs py-2 rounded border border-[#30363d] text-[#ef4444]
                     hover:bg-[#21262d] hover:text-[#fca5a5] transition-colors flex items-center justify-center gap-1"
        >
          PDF
        </button>
        <button
          onClick={() => window.open(`${API_BASE_URL}/api/export/${jobId}/report?format=docx`)}
          className="flex-1 text-xs py-2 rounded border border-[#30363d] text-[#58a6ff]
                     hover:bg-[#21262d] hover:text-[#79c0ff] transition-colors flex items-center justify-center gap-1"
        >
          Word
        </button>
        <button
          onClick={() => window.open(`${API_BASE_URL}/api/export/${jobId}/report?format=json`)}
          className="flex-1 text-xs py-2 rounded border border-[#30363d] text-[#8b949e]
                     hover:bg-[#21262d] hover:text-[#e6edf3] transition-colors flex items-center justify-center gap-1"
        >
          JSON
        </button>
      </div>
    </div>
  )
}
