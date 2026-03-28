import { useEffect, useState } from "react"
import { API_BASE_URL } from "../config"

const C = {
  bgPanel: "#161b22",
  bgBase: "#0d1117",
  border: "#30363d",
  text1: "#e6edf3",
  text2: "#8b949e",
  accent: "#58a6ff",
  yellow: "#d29922",
  green: "#3fb950",
  red: "#f85149",
} as const

interface Brief {
  alert_id: string
  title: string
  summary: string
  legal_context: string
  recommendations: string[]
  risk_level: string
  generated_at: string
}

interface StrategicOverview {
  high_risk_zones: number
  active_alerts: number
  pending_inspections: number
  weekly_trend: string
  top_regions: { name: string; risk_score: number }[]
}

interface StrategicPanelProps {
  token: string
}

export default function StrategicPanel({ token }: StrategicPanelProps) {
  const [tab, setTab] = useState<"overview" | "briefs" | "reports">("overview")
  const [overview, setOverview] = useState<StrategicOverview | null>(null)
  const [briefs, setBriefs] = useState<Brief[]>([])
  const [loading, setLoading] = useState(false)

  const headers = {
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json",
  }

  const fetchOverview = async () => {
    setLoading(true)
    try {
      const res = await fetch(`${API_BASE_URL}/api/strategic/overview`, { headers })
      if (res.ok) setOverview(await res.json())
    } catch { /* ignore */ }
    setLoading(false)
  }

  useEffect(() => {
    if (tab === "overview") fetchOverview()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab])

  const fetchBrief = async (alertId: string) => {
    setLoading(true)
    try {
      const res = await fetch(`${API_BASE_URL}/api/brief/${alertId}`, { headers })
      if (res.ok) {
        const brief = await res.json()
        setBriefs((prev) => [brief, ...prev.filter((b) => b.alert_id !== alertId)])
      }
    } catch { /* ignore */ }
    setLoading(false)
  }

  const riskColor = (level: string) => {
    if (level === "high" || level === "critical") return C.red
    if (level === "medium") return C.yellow
    return C.green
  }

  return (
    <div className="h-full flex flex-col" style={{ color: C.text1 }}>
      {/* Tab bar */}
      <div className="flex gap-1 p-2 border-b" style={{ borderColor: C.border }}>
        {(["overview", "briefs", "reports"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className="px-3 py-1 rounded text-xs font-medium transition-colors"
            style={{
              backgroundColor: tab === t ? C.accent + "22" : "transparent",
              color: tab === t ? C.accent : C.text2,
            }}
          >
            {t === "overview" ? "Vista General" : t === "briefs" ? "Briefings" : "Reportes"}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {loading && (
          <p className="text-xs animate-pulse" style={{ color: C.text2 }}>
            Cargando...
          </p>
        )}

        {/* Overview */}
        {tab === "overview" && overview && (
          <>
            <div className="grid grid-cols-2 gap-2">
              <StatCard label="Zonas alto riesgo" value={overview.high_risk_zones} color={C.red} />
              <StatCard label="Alertas activas" value={overview.active_alerts} color={C.yellow} />
              <StatCard label="Inspecciones pend." value={overview.pending_inspections} color={C.accent} />
              <StatCard
                label="Tendencia semanal"
                value={overview.weekly_trend}
                color={overview.weekly_trend === "up" ? C.red : C.green}
              />
            </div>

            <h3 className="text-xs font-bold mt-4" style={{ color: C.text2 }}>
              Regiones Prioritarias
            </h3>
            {overview.top_regions.map((r, i) => (
              <div
                key={i}
                className="flex items-center justify-between px-2 py-1.5 rounded"
                style={{ backgroundColor: C.bgBase }}
              >
                <span className="text-xs">{r.name}</span>
                <div className="flex items-center gap-2">
                  <div
                    className="h-1.5 rounded-full"
                    style={{
                      width: `${Math.min(r.risk_score * 100, 100)}px`,
                      backgroundColor: riskColor(r.risk_score > 0.7 ? "high" : r.risk_score > 0.4 ? "medium" : "low"),
                    }}
                  />
                  <span className="text-xs font-mono" style={{ color: C.text2 }}>
                    {(r.risk_score * 100).toFixed(0)}%
                  </span>
                </div>
              </div>
            ))}
          </>
        )}

        {/* Briefs */}
        {tab === "briefs" && (
          <>
            <div className="flex items-center gap-2 mb-2">
              <input
                type="text"
                placeholder="ID de alerta..."
                className="flex-1 rounded px-2 py-1 text-xs outline-none"
                style={{ backgroundColor: C.bgBase, border: `1px solid ${C.border}`, color: C.text1 }}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    const input = e.currentTarget
                    if (input.value.trim()) {
                      fetchBrief(input.value.trim())
                      input.value = ""
                    }
                  }
                }}
              />
            </div>
            {briefs.map((b) => (
              <div
                key={b.alert_id}
                className="rounded-lg p-3 space-y-2"
                style={{ backgroundColor: C.bgBase, border: `1px solid ${C.border}` }}
              >
                <div className="flex items-center justify-between">
                  <h4 className="text-xs font-bold">{b.title || `Alerta ${b.alert_id}`}</h4>
                  <span
                    className="text-[10px] px-1.5 py-0.5 rounded-full font-medium"
                    style={{ backgroundColor: riskColor(b.risk_level) + "22", color: riskColor(b.risk_level) }}
                  >
                    {b.risk_level}
                  </span>
                </div>
                <p className="text-xs leading-relaxed" style={{ color: C.text2 }}>
                  {b.summary}
                </p>
                {b.legal_context && (
                  <div
                    className="text-[10px] px-2 py-1 rounded"
                    style={{ backgroundColor: C.yellow + "11", color: C.yellow }}
                  >
                    {b.legal_context}
                  </div>
                )}
                {b.recommendations?.length > 0 && (
                  <ul className="text-xs space-y-1 pl-3" style={{ color: C.text2 }}>
                    {b.recommendations.map((r, i) => (
                      <li key={i} className="list-disc">{r}</li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
          </>
        )}

        {/* Reports */}
        {tab === "reports" && (
          <div className="text-xs space-y-2" style={{ color: C.text2 }}>
            <p>Los reportes semanales se generan automáticamente cada lunes a las 6:00 AM.</p>
            <button
              onClick={async () => {
                try {
                  const res = await fetch(`${API_BASE_URL}/api/strategic/export-pdf`, { headers })
                  if (res.ok) {
                    const blob = await res.blob()
                    const url = URL.createObjectURL(blob)
                    const a = document.createElement("a")
                    a.href = url
                    a.download = "reporte_semanal_apex.pdf"
                    a.click()
                    URL.revokeObjectURL(url)
                  }
                } catch { /* ignore */ }
              }}
              className="px-3 py-1.5 rounded text-xs font-medium"
              style={{ backgroundColor: C.accent + "22", color: C.accent }}
            >
              Descargar último reporte (PDF)
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

function StatCard({ label, value, color }: { label: string; value: string | number; color: string }) {
  return (
    <div
      className="rounded-lg p-3 text-center"
      style={{ backgroundColor: "#0d1117", border: `1px solid #30363d` }}
    >
      <p className="text-lg font-bold" style={{ color }}>{value}</p>
      <p className="text-[10px] mt-0.5" style={{ color: "#8b949e" }}>{label}</p>
    </div>
  )
}
