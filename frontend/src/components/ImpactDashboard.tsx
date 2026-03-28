import { useEffect, useState } from "react"
import {
  BarChart, Bar, LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, PieChart, Pie, Cell,
} from "recharts"
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
  purple: "#a371f7",
} as const

const PIE_COLORS = [C.accent, C.green, C.yellow, C.red, C.purple, "#f0883e", "#79c0ff", "#d2a8ff"]

interface KpiSummary {
  period_days: number
  total_jobs: number
  completed_jobs: number
  completion_rate: number
  total_alerts: number
  total_detections: number
  validated_detections: number
  validation_rate: number
  avg_response_seconds: number
}

interface EngineMetric {
  engine: string
  total_detections: number
  validated: number
  rejected: number
  precision: number
}

interface TimelinePoint {
  period: string
  detections: number
  alerts: number
}

interface RetrainStatus {
  engine: string
  validated_labels: number
  ready_to_retrain: boolean
  label_threshold: number
  best_f1: number | null
  total_runs: number
}

interface ImpactDashboardProps {
  token: string
}

export default function ImpactDashboard({ token }: ImpactDashboardProps) {
  const [period, setPeriod] = useState(30)
  const [summary, setSummary] = useState<KpiSummary | null>(null)
  const [engines, setEngines] = useState<EngineMetric[]>([])
  const [timeline, setTimeline] = useState<TimelinePoint[]>([])
  const [retraining, setRetraining] = useState<RetrainStatus[]>([])
  const [loading, setLoading] = useState(false)

  const headers = { Authorization: `Bearer ${token}` }

  useEffect(() => {
    fetchAll()
  }, [period])

  const fetchAll = async () => {
    setLoading(true)
    try {
      const [sumRes, engRes, tlRes, rtRes] = await Promise.all([
        fetch(`${API_BASE_URL}/api/kpi/summary?days=${period}`, { headers }),
        fetch(`${API_BASE_URL}/api/kpi/engines?days=${period}`, { headers }),
        fetch(`${API_BASE_URL}/api/kpi/timeline?days=${period}&granularity=day`, { headers }),
        fetch(`${API_BASE_URL}/api/kpi/retraining`, { headers }),
      ])
      if (sumRes.ok) setSummary(await sumRes.json())
      if (engRes.ok) {
        const d = await engRes.json()
        setEngines(d.engines || [])
      }
      if (tlRes.ok) {
        const d = await tlRes.json()
        setTimeline(d.timeline || [])
      }
      if (rtRes.ok) {
        const d = await rtRes.json()
        setRetraining(d.engines || [])
      }
    } catch { /* ignore */ }
    setLoading(false)
  }

  return (
    <div className="h-full flex flex-col" style={{ color: C.text1 }}>
      {/* Header */}
      <div className="flex items-center justify-between p-3 border-b" style={{ borderColor: C.border }}>
        <h3 className="text-xs font-bold" style={{ color: C.green }}>
          Dashboard de Impacto
        </h3>
        <select
          value={period}
          onChange={(e) => setPeriod(Number(e.target.value))}
          className="rounded px-2 py-0.5 text-xs outline-none"
          style={{ backgroundColor: C.bgBase, border: `1px solid ${C.border}`, color: C.text1 }}
        >
          <option value={7}>7 días</option>
          <option value={30}>30 días</option>
          <option value={90}>90 días</option>
          <option value={365}>1 año</option>
        </select>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-4">
        {loading && (
          <p className="text-xs animate-pulse" style={{ color: C.text2 }}>Cargando métricas...</p>
        )}

        {/* Summary cards */}
        {summary && (
          <div className="grid grid-cols-3 gap-2">
            <MiniCard label="Jobs" value={summary.total_jobs} sub={`${(summary.completion_rate * 100).toFixed(0)}% completados`} color={C.accent} />
            <MiniCard label="Alertas" value={summary.total_alerts} color={C.yellow} />
            <MiniCard label="Detecciones" value={summary.total_detections} sub={`${summary.validated_detections} validadas`} color={C.green} />
            <MiniCard label="Precisión" value={`${(summary.validation_rate * 100).toFixed(0)}%`} color={summary.validation_rate > 0.7 ? C.green : C.yellow} />
            <MiniCard label="T. Respuesta" value={`${(summary.avg_response_seconds / 60).toFixed(1)} min`} color={C.accent} />
            <MiniCard label="Período" value={`${summary.period_days}d`} color={C.text2} />
          </div>
        )}

        {/* Timeline chart */}
        {timeline.length > 0 && (
          <div>
            <h4 className="text-[10px] font-bold mb-1" style={{ color: C.text2 }}>Detecciones y Alertas</h4>
            <div className="rounded-lg p-2" style={{ backgroundColor: C.bgBase, border: `1px solid ${C.border}` }}>
              <ResponsiveContainer width="100%" height={140}>
                <LineChart data={timeline}>
                  <CartesianGrid stroke={C.border} strokeDasharray="3 3" />
                  <XAxis dataKey="period" tick={{ fontSize: 9, fill: C.text2 }} tickFormatter={(v: string) => v.slice(5)} />
                  <YAxis tick={{ fontSize: 9, fill: C.text2 }} width={30} />
                  <Tooltip
                    contentStyle={{ backgroundColor: C.bgPanel, border: `1px solid ${C.border}`, fontSize: 11 }}
                    labelStyle={{ color: C.text1 }}
                  />
                  <Line type="monotone" dataKey="detections" stroke={C.green} strokeWidth={1.5} dot={false} name="Detecciones" />
                  <Line type="monotone" dataKey="alerts" stroke={C.yellow} strokeWidth={1.5} dot={false} name="Alertas" />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        )}

        {/* Engines bar chart */}
        {engines.length > 0 && (
          <div>
            <h4 className="text-[10px] font-bold mb-1" style={{ color: C.text2 }}>Rendimiento por Motor</h4>
            <div className="rounded-lg p-2" style={{ backgroundColor: C.bgBase, border: `1px solid ${C.border}` }}>
              <ResponsiveContainer width="100%" height={140}>
                <BarChart data={engines} layout="vertical">
                  <CartesianGrid stroke={C.border} strokeDasharray="3 3" />
                  <XAxis type="number" tick={{ fontSize: 9, fill: C.text2 }} />
                  <YAxis type="category" dataKey="engine" tick={{ fontSize: 9, fill: C.text2 }} width={80} />
                  <Tooltip
                    contentStyle={{ backgroundColor: C.bgPanel, border: `1px solid ${C.border}`, fontSize: 11 }}
                    labelStyle={{ color: C.text1 }}
                  />
                  <Bar dataKey="validated" stackId="a" fill={C.green} name="Validadas" />
                  <Bar dataKey="rejected" stackId="a" fill={C.red} name="Rechazadas" />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        )}

        {/* Detection distribution pie */}
        {engines.length > 0 && (
          <div>
            <h4 className="text-[10px] font-bold mb-1" style={{ color: C.text2 }}>Distribución por Motor</h4>
            <div className="rounded-lg p-2 flex justify-center" style={{ backgroundColor: C.bgBase, border: `1px solid ${C.border}` }}>
              <ResponsiveContainer width="100%" height={140}>
                <PieChart>
                  <Pie
                    data={engines.map((e) => ({ name: e.engine, value: e.total_detections }))}
                    cx="50%"
                    cy="50%"
                    innerRadius={30}
                    outerRadius={55}
                    dataKey="value"
                    label={({ name }: { name: string }) => name}
                    labelLine={false}
                    fontSize={9}
                  >
                    {engines.map((_, i) => (
                      <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip
                    contentStyle={{ backgroundColor: C.bgPanel, border: `1px solid ${C.border}`, fontSize: 11 }}
                  />
                </PieChart>
              </ResponsiveContainer>
            </div>
          </div>
        )}

        {/* Retraining status */}
        {retraining.length > 0 && (
          <div>
            <h4 className="text-[10px] font-bold mb-1" style={{ color: C.text2 }}>Estado de Reentrenamiento</h4>
            <div className="space-y-1">
              {retraining.map((r) => (
                <div
                  key={r.engine}
                  className="flex items-center justify-between px-2 py-1.5 rounded text-xs"
                  style={{ backgroundColor: C.bgBase, border: `1px solid ${C.border}` }}
                >
                  <div className="flex items-center gap-2">
                    <span
                      className="w-1.5 h-1.5 rounded-full"
                      style={{ backgroundColor: r.ready_to_retrain ? C.green : C.text2 }}
                    />
                    <span>{r.engine}</span>
                  </div>
                  <div className="flex items-center gap-3" style={{ color: C.text2 }}>
                    <span>{r.validated_labels}/{r.label_threshold} labels</span>
                    {r.best_f1 != null && <span>F1: {(r.best_f1 * 100).toFixed(1)}%</span>}
                    {r.total_runs > 0 && <span>{r.total_runs} runs</span>}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function MiniCard({ label, value, sub, color }: { label: string; value: string | number; sub?: string; color: string }) {
  return (
    <div className="rounded-lg p-2 text-center" style={{ backgroundColor: "#0d1117", border: `1px solid #30363d` }}>
      <p className="text-sm font-bold" style={{ color }}>{value}</p>
      <p className="text-[10px]" style={{ color: "#8b949e" }}>{label}</p>
      {sub && <p className="text-[9px]" style={{ color: "#8b949e" }}>{sub}</p>}
    </div>
  )
}
