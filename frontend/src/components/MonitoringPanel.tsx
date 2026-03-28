import { useState, useEffect, useCallback, useRef } from "react"
import { API_BASE_URL } from "../config"

/* -- Design tokens ---------------------------------------- */
const C = {
  bgCard: "#21262d",
  bgCardHover: "#282e36",
  border: "#30363d",
  text1: "#e6edf3",
  text2: "#8b949e",
  blue: "#58a6ff",
  green: "#2ea043",
  orange: "#f0883e",
  red: "#f85149",
  purple: "#bc8cff",
  cyan: "#06b6d4",
  yellow: "#facc15",
} as const

/* -- Engine color mapping (matches backend alert_service) - */
const ENGINE_COLORS: Record<string, string> = {
  deforestation: "#f85149",
  vegetation: "#2ea043",
  urban_expansion: "#f0883e",
  structures: "#58a6ff",
  hansen: "#facc15",
  alerts: "#dc2626",
  drivers: "#8b5cf6",
  fire: "#f97316",
  sar: "#06b6d4",
  firms_hotspots: "#ff3b30",
  crossval: "#10b981",
  legal_context: "#22c55e",
}

/* -- Interfaces ------------------------------------------- */
interface MonitoredArea {
  id: number
  name: string
  aoi_geojson: string
  engines: string
  alert_email: string
  alert_threshold_ha: number
  check_interval_hours: number
  last_checked: string | null
  active: boolean
  created_at: string
  alert_count?: number
}

interface MonitoringAlert {
  id: number
  monitoring_area_id: number
  detected_at: string
  alert_type: string
  area_ha: number
  details_json: string
  email_sent: boolean
}

interface MonitoringPanelProps {
  aoi: object | null
}

/* -- Constants -------------------------------------------- */
const INTERVAL_OPTIONS = [
  { value: 6, label: "Cada 6 h" },
  { value: 12, label: "Cada 12 h" },
  { value: 24, label: "Cada 24 h" },
  { value: 48, label: "Cada 48 h" },
  { value: 168, label: "Cada 1 semana" },
  { value: 336, label: "Cada 2 semanas" },
  { value: 720, label: "Cada 1 mes" },
]

const ENGINE_OPTIONS = [
  { id: "deforestation", label: "Deforestacion", desc: "Dynamic World cambio cobertura" },
  { id: "urban_expansion", label: "Expansion urbana", desc: "Crecimiento de zonas edificadas" },
  { id: "vegetation", label: "Vegetacion", desc: "Clasificacion de cobertura vegetal" },
  { id: "fire", label: "Incendios", desc: "MODIS area quemada" },
  { id: "alerts", label: "Alertas GLAD/RADD", desc: "Alertas de deforestacion en tiempo real" },
  { id: "hansen", label: "Hansen GFC", desc: "Perdida forestal historica (UMD)" },
  { id: "sar", label: "SAR Sentinel-1", desc: "Deteccion por radar" },
  { id: "drivers", label: "Drivers WRI", desc: "Causas de perdida forestal" },
  { id: "firms_hotspots", label: "FIRMS NRT", desc: "Hotspots activos NASA FIRMS (VIIRS/MODIS)" },
]

const THRESHOLD_PRESETS = [
  { value: 0.5, label: "0.5 ha (alta sensibilidad)" },
  { value: 1.0, label: "1 ha (estandar)" },
  { value: 5.0, label: "5 ha (moderado)" },
  { value: 10.0, label: "10 ha (bajo)" },
  { value: 25.0, label: "25 ha (muy bajo)" },
  { value: 50.0, label: "50 ha (minimo)" },
]

/* -- Helpers ---------------------------------------------- */
function relativeTime(dateStr: string | null): string {
  if (!dateStr) return "Nunca"
  const diff = Date.now() - new Date(dateStr).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return "Justo ahora"
  if (mins < 60) return `hace ${mins} min`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `hace ${hours} h`
  const days = Math.floor(hours / 24)
  return `hace ${days} d`
}

function formatDate(dateStr: string): string {
  const d = new Date(dateStr)
  return d.toLocaleDateString("es-MX", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  })
}

function nextCheckEstimate(lastChecked: string | null, intervalHours: number): string {
  if (!lastChecked) return "Pendiente"
  const next = new Date(lastChecked).getTime() + intervalHours * 3600_000
  const diff = next - Date.now()
  if (diff <= 0) return "Pronto"
  const hours = Math.floor(diff / 3600_000)
  if (hours < 1) return `en ${Math.ceil(diff / 60_000)} min`
  if (hours < 24) return `en ${hours} h`
  return `en ${Math.floor(hours / 24)} d`
}

function parseEngines(raw: string): string[] {
  try {
    return JSON.parse(raw)
  } catch {
    return []
  }
}

function getHealthColor(area: MonitoredArea): string {
  const count = area.alert_count ?? 0
  if (!area.active) return C.text2
  if (count === 0) return C.green
  if (count <= 3) return C.yellow
  if (count <= 10) return C.orange
  return C.red
}

function getHealthLabel(area: MonitoredArea): string {
  const count = area.alert_count ?? 0
  if (!area.active) return "Inactivo"
  if (count === 0) return "Sin alertas"
  if (count <= 3) return "Atencion"
  if (count <= 10) return "Alerta"
  return "Critico"
}

function formatInterval(hours: number): string {
  if (hours < 24) return `${hours}h`
  if (hours < 168) return `${Math.round(hours / 24)}d`
  if (hours < 720) return `${Math.round(hours / 168)}sem`
  return `${Math.round(hours / 720)}mes`
}

/* -- Shared style ----------------------------------------- */
const inputStyle: React.CSSProperties = {
  backgroundColor: C.bgCard,
  border: `1px solid ${C.border}`,
  color: C.text1,
}

/* -- Tiny Button ------------------------------------------ */
function Btn({
  children,
  color,
  onClick,
  disabled,
  small,
  full,
}: {
  children: React.ReactNode
  color: string
  onClick: () => void
  disabled?: boolean
  small?: boolean
  full?: boolean
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`${full ? "flex-1" : ""} ${small ? "px-2 py-0.5 text-[9px]" : "px-3 py-1.5 text-xs"} rounded-md font-semibold transition-all disabled:opacity-30 disabled:cursor-not-allowed hover:brightness-110`}
      style={{ backgroundColor: color, color: "#fff" }}
    >
      {children}
    </button>
  )
}

/* ========================================================= */
/* -- Component -------------------------------------------- */
/* ========================================================= */
export default function MonitoringPanel({ aoi }: MonitoringPanelProps) {
  /* ---- state ---- */
  const [areas, setAreas] = useState<MonitoredArea[]>([])
  const [loading, setLoading] = useState(false)
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [history, setHistory] = useState<MonitoringAlert[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [showForm, setShowForm] = useState(false)
  const [autoRefresh, setAutoRefresh] = useState(true)
  const autoRefreshRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const [filterActive, setFilterActive] = useState<"all" | "active" | "inactive">("all")

  // form
  const [name, setName] = useState("")
  const [email, setEmail] = useState("")
  const [threshold, setThreshold] = useState(1.0)
  const [checkInterval, setCheckInterval] = useState(24)
  const [engines, setEngines] = useState<string[]>(["deforestation", "urban_expansion"])
  const [notes, setNotes] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [testingEmail, setTestingEmail] = useState(false)
  const [analyzingId, setAnalyzingId] = useState<number | null>(null)
  const [togglingId, setTogglingId] = useState<number | null>(null)
  const [statusMsg, setStatusMsg] = useState<{ text: string; ok: boolean } | null>(null)

  /* ---- fetch areas ---- */
  const fetchAreas = useCallback(async () => {
    setLoading(true)
    try {
      const res = await fetch(`${API_BASE_URL}/api/monitoring`)
      if (res.ok) {
        const data = await res.json()
        setAreas(Array.isArray(data) ? data : (data.areas ?? []))
      }
    } catch {
      /* silently ignore */
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchAreas()
  }, [fetchAreas])

  /* ---- auto-refresh every 30s ---- */
  useEffect(() => {
    if (autoRefresh) {
      autoRefreshRef.current = window.setInterval(fetchAreas, 30_000)
    }
    return () => {
      if (autoRefreshRef.current) clearInterval(autoRefreshRef.current)
    }
  }, [autoRefresh, fetchAreas])

  /* ---- flash status ---- */
  const flash = (text: string, ok: boolean) => {
    setStatusMsg({ text, ok })
    setTimeout(() => setStatusMsg(null), 4000)
  }

  /* ---- engine checkbox toggle ---- */
  const toggleEngine = (id: string) => {
    setEngines((prev) =>
      prev.includes(id) ? prev.filter((e) => e !== id) : [...prev, id],
    )
  }

  /* ---- select all / none engines ---- */
  const selectAllEngines = () => setEngines(ENGINE_OPTIONS.map((e) => e.id))
  const selectNoneEngines = () => setEngines([])

  /* ---- register ---- */
  const handleRegister = useCallback(async () => {
    if (!aoi || !name.trim() || !email.trim()) return
    if (engines.length === 0) {
      flash("Selecciona al menos un motor de analisis", false)
      return
    }
    setSubmitting(true)
    try {
      const body = {
        name: name.trim(),
        aoi_geojson: aoi,
        engines,
        alert_email: email.trim(),
        threshold_ha: threshold,
        interval_hours: checkInterval,
        notes: notes.trim() || undefined,
      }
      console.log("[Monitoring] POST body:", JSON.stringify(body))
      const res = await fetch(`${API_BASE_URL}/api/monitoring`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
      if (res.ok) {
        setName("")
        setEmail("")
        setThreshold(1.0)
        setCheckInterval(24)
        setEngines(["deforestation", "urban_expansion"])
        setNotes("")
        setShowForm(false)
        await fetchAreas()
        flash("Area registrada para monitoreo", true)
      } else {
        const err = await res.json()
        console.error("[Monitoring] Error:", err)
        flash(`Error: ${JSON.stringify(err.detail).slice(0, 100)}`, false)
      }
    } catch (e) {
      flash(`Error de red: ${e}`, false)
    } finally {
      setSubmitting(false)
    }
  }, [aoi, name, email, threshold, checkInterval, engines, notes, fetchAreas])

  /* ---- test email ---- */
  const handleTestEmail = useCallback(async () => {
    if (!email.trim()) {
      flash("Ingresa un email primero", false)
      return
    }
    setTestingEmail(true)
    try {
      const res = await fetch(`${API_BASE_URL}/api/monitoring/test-email`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim(), area_name: name.trim() || "Prueba APEX" }),
      })
      if (res.ok) {
        flash(`Email enviado a ${email}`, true)
      } else {
        const err = await res.json()
        flash(`Error SMTP: ${typeof err.detail === "string" ? err.detail.slice(0, 80) : JSON.stringify(err.detail).slice(0, 80)}`, false)
      }
    } catch (e) {
      flash(`Error de red: ${e}`, false)
    } finally {
      setTestingEmail(false)
    }
  }, [email, name])

  /* ---- delete ---- */
  const handleDelete = useCallback(
    async (id: number, areaName: string) => {
      if (!confirm(`Eliminar permanentemente "${areaName}" y todas sus alertas?`)) return
      try {
        const res = await fetch(`${API_BASE_URL}/api/monitoring/${id}`, { method: "DELETE" })
        if (res.ok) {
          setAreas((prev) => prev.filter((a) => a.id !== id))
          if (expandedId === id) {
            setExpandedId(null)
            setHistory([])
          }
          flash("Area eliminada permanentemente", true)
        }
      } catch {
        flash("Error al eliminar", false)
      }
    },
    [expandedId],
  )

  /* ---- toggle active/inactive ---- */
  const handleToggle = useCallback(
    async (area: MonitoredArea) => {
      setTogglingId(area.id)
      try {
        const res = await fetch(`${API_BASE_URL}/api/monitoring/${area.id}/toggle`, {
          method: "PATCH",
        })
        if (res.ok) {
          const data = await res.json()
          setAreas((prev) =>
            prev.map((a) => (a.id === area.id ? { ...a, active: data.active } : a)),
          )
          flash(`${area.name}: ${data.active ? "Activado" : "Desactivado"}`, true)
        }
      } catch {
        flash("Error al cambiar estado", false)
      } finally {
        setTogglingId(null)
      }
    },
    [],
  )

  /* ---- analyze now ---- */
  const handleAnalyzeNow = useCallback(
    async (area: MonitoredArea) => {
      setAnalyzingId(area.id)
      flash(`Analizando ${area.name}... esto puede tardar 1-3 min. Se enviara reporte por email.`, true)
      try {
        const res = await fetch(`${API_BASE_URL}/api/monitoring/${area.id}/analyze`, {
          method: "POST",
        })
        if (res.ok) {
          // Poll for completion
          const pollInterval = window.setInterval(async () => {
            const r = await fetch(`${API_BASE_URL}/api/monitoring`)
            if (r.ok) {
              const d = await r.json()
              const updated = (d.areas ?? d).find?.((a: MonitoredArea) => a.id === area.id)
              if (updated && updated.last_checked !== area.last_checked) {
                clearInterval(pollInterval)
                setAreas((prev) =>
                  prev.map((a) => (a.id === area.id ? { ...a, ...updated } : a)),
                )
                setAnalyzingId(null)
                flash(`Analisis de ${area.name} completado. Reporte enviado a ${area.alert_email}`, true)
              }
            }
          }, 5000)
          // Safety timeout: stop polling after 5 minutes
          setTimeout(() => {
            clearInterval(pollInterval)
            setAnalyzingId(null)
            fetchAreas()
          }, 300_000)
        } else {
          flash("Error al iniciar analisis", false)
          setAnalyzingId(null)
        }
      } catch {
        flash("Error de red", false)
        setAnalyzingId(null)
      }
    },
    [fetchAreas],
  )

  /* ---- purge alerts ---- */
  const handlePurgeAlerts = useCallback(
    async (area: MonitoredArea) => {
      if (!confirm(`Borrar todas las alertas de "${area.name}"?`)) return
      try {
        const res = await fetch(`${API_BASE_URL}/api/monitoring/${area.id}/alerts`, {
          method: "DELETE",
        })
        if (res.ok) {
          const data = await res.json()
          flash(`${data.deleted} alertas eliminadas`, true)
          setHistory([])
          setExpandedId(null)
          await fetchAreas()
        }
      } catch {
        flash("Error al purgar alertas", false)
      }
    },
    [fetchAreas],
  )

  /* ---- history ---- */
  const toggleHistory = useCallback(
    async (id: number) => {
      if (expandedId === id) {
        setExpandedId(null)
        setHistory([])
        return
      }
      setExpandedId(id)
      setHistoryLoading(true)
      try {
        const res = await fetch(`${API_BASE_URL}/api/monitoring/${id}/history`)
        if (res.ok) {
          const data = await res.json()
          setHistory(Array.isArray(data) ? data : (data.alerts ?? []))
        }
      } catch {
        setHistory([])
      } finally {
        setHistoryLoading(false)
      }
    },
    [expandedId],
  )

  /* ---- computed stats ---- */
  const totalAlerts = areas.reduce((sum, a) => sum + (a.alert_count ?? 0), 0)
  const activeAreas = areas.filter((a) => a.active).length
  const criticalAreas = areas.filter((a) => (a.alert_count ?? 0) > 10).length

  /* ---- filtered areas ---- */
  const filteredAreas = areas.filter((a) => {
    if (filterActive === "active") return a.active
    if (filterActive === "inactive") return !a.active
    return true
  })

  /* ---- render ---- */
  return (
    <div className="space-y-3">
      {/* Status flash */}
      {statusMsg && (
        <div
          className="rounded-md px-3 py-2 text-xs font-medium animate-pulse"
          style={{
            backgroundColor: statusMsg.ok ? C.green + "22" : C.red + "22",
            color: statusMsg.ok ? C.green : C.red,
            border: `1px solid ${statusMsg.ok ? C.green + "44" : C.red + "44"}`,
          }}
        >
          {statusMsg.text}
        </div>
      )}

      {/* Dashboard Summary */}
      {areas.length > 0 && (
        <div className="grid grid-cols-4 gap-1.5">
          {[
            { label: "Areas", value: areas.length, color: C.blue },
            { label: "Activas", value: activeAreas, color: C.green },
            { label: "Alertas", value: totalAlerts, color: C.orange },
            { label: "Criticas", value: criticalAreas, color: criticalAreas > 0 ? C.red : C.text2 },
          ].map((stat) => (
            <div
              key={stat.label}
              className="rounded-md px-2 py-1.5 text-center"
              style={{ backgroundColor: stat.color + "12", border: `1px solid ${stat.color}22` }}
            >
              <div className="text-sm font-bold" style={{ color: stat.color }}>
                {stat.value}
              </div>
              <div className="text-[8px] uppercase tracking-wider" style={{ color: C.text2 }}>
                {stat.label}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Header + toggle form */}
      <div className="flex items-center justify-between">
        <p
          className="text-[10px] font-semibold uppercase tracking-wider"
          style={{ color: C.text2 }}
        >
          Registrar monitoreo
        </p>
        <div className="flex items-center gap-1.5">
          {/* Auto-refresh toggle */}
          <button
            onClick={() => setAutoRefresh(!autoRefresh)}
            className="text-[9px] px-1.5 py-0.5 rounded transition-all"
            style={{
              backgroundColor: autoRefresh ? C.green + "22" : "transparent",
              color: autoRefresh ? C.green : C.text2,
              border: `1px solid ${autoRefresh ? C.green + "44" : C.border}`,
            }}
            title={autoRefresh ? "Auto-refresco activo (30s)" : "Auto-refresco desactivado"}
          >
            {autoRefresh ? "Auto" : "Manual"}
          </button>
          {aoi && (
            <button
              onClick={() => setShowForm(!showForm)}
              className="text-[10px] font-medium px-2 py-0.5 rounded transition-all hover:brightness-110"
              style={{
                backgroundColor: showForm ? C.red + "22" : C.blue + "22",
                color: showForm ? C.red : C.blue,
                border: `1px solid ${showForm ? C.red + "44" : C.blue + "44"}`,
              }}
            >
              {showForm ? "Cerrar" : "+ Nuevo"}
            </button>
          )}
        </div>
      </div>

      {/* Register form (collapsible) */}
      {showForm && aoi ? (
        <div
          className="rounded-md p-2.5 space-y-1.5"
          style={{ backgroundColor: C.bgCard, border: `1px solid ${C.border}` }}
        >
          <input
            type="text"
            placeholder="Nombre del area (ej: Sierra Norte, Parcela 42)"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="w-full rounded-md px-2.5 py-1.5 text-xs outline-none placeholder:text-gray-500"
            style={inputStyle}
          />
          <input
            type="email"
            placeholder="Email para reportes automaticos"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full rounded-md px-2.5 py-1.5 text-xs outline-none placeholder:text-gray-500"
            style={inputStyle}
          />

          {/* Threshold presets */}
          <div>
            <label className="text-[10px] mb-0.5 block" style={{ color: C.text2 }}>
              Umbral de alerta
            </label>
            <select
              value={threshold}
              onChange={(e) => setThreshold(parseFloat(e.target.value))}
              className="w-full rounded-md px-2.5 py-1.5 text-xs outline-none cursor-pointer"
              style={inputStyle}
            >
              {THRESHOLD_PRESETS.map((p) => (
                <option key={p.value} value={p.value}>{p.label}</option>
              ))}
            </select>
            <div className="mt-1">
              <input
                type="range"
                min={0.1}
                max={100}
                step={0.1}
                value={threshold}
                onChange={(e) => setThreshold(parseFloat(e.target.value))}
                className="w-full h-1 rounded cursor-pointer"
                style={{ accentColor: C.blue }}
              />
              <div className="flex justify-between text-[8px]" style={{ color: C.text2 }}>
                <span>0.1 ha</span>
                <span className="font-medium" style={{ color: C.text1 }}>{threshold} ha</span>
                <span>100 ha</span>
              </div>
            </div>
          </div>

          {/* Interval */}
          <div>
            <label className="text-[10px] mb-0.5 block" style={{ color: C.text2 }}>
              Frecuencia de verificacion
            </label>
            <select
              value={checkInterval}
              onChange={(e) => setCheckInterval(Number(e.target.value))}
              className="w-full rounded-md px-2.5 py-1.5 text-xs outline-none cursor-pointer"
              style={inputStyle}
            >
              {INTERVAL_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>

          {/* Engine checkboxes with descriptions */}
          <div>
            <div className="flex items-center justify-between mb-1">
              <label className="text-[10px]" style={{ color: C.text2 }}>
                Motores de analisis ({engines.length}/{ENGINE_OPTIONS.length})
              </label>
              <div className="flex gap-1">
                <button
                  onClick={selectAllEngines}
                  className="text-[8px] px-1 rounded hover:brightness-125"
                  style={{ color: C.blue }}
                >
                  Todos
                </button>
                <button
                  onClick={selectNoneEngines}
                  className="text-[8px] px-1 rounded hover:brightness-125"
                  style={{ color: C.text2 }}
                >
                  Ninguno
                </button>
              </div>
            </div>
            <div className="space-y-0.5">
              {ENGINE_OPTIONS.map((eng) => {
                const eColor = ENGINE_COLORS[eng.id] || C.purple
                const selected = engines.includes(eng.id)
                return (
                  <label
                    key={eng.id}
                    className="flex items-center gap-1.5 text-[10px] cursor-pointer rounded px-1.5 py-1 transition-all"
                    style={{
                      backgroundColor: selected ? eColor + "15" : "transparent",
                      border: `1px solid ${selected ? eColor + "44" : "transparent"}`,
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={selected}
                      onChange={() => toggleEngine(eng.id)}
                      className="sr-only"
                    />
                    <span
                      className="w-2.5 h-2.5 rounded-sm flex-shrink-0"
                      style={{
                        backgroundColor: selected ? eColor : "transparent",
                        border: `1.5px solid ${selected ? eColor : C.border}`,
                      }}
                    />
                    <div className="flex-1 min-w-0">
                      <span style={{ color: selected ? eColor : C.text2, fontWeight: selected ? 600 : 400 }}>
                        {eng.label}
                      </span>
                      <span className="text-[8px] ml-1" style={{ color: C.text2 }}>
                        {eng.desc}
                      </span>
                    </div>
                  </label>
                )
              })}
            </div>
          </div>

          {/* Notes */}
          <textarea
            placeholder="Notas opcionales (ej: denuncia PROFEPA-123, zona ejidal...)"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={2}
            className="w-full rounded-md px-2.5 py-1.5 text-xs outline-none placeholder:text-gray-500 resize-none"
            style={inputStyle}
          />

          {/* Info banner */}
          <div
            className="rounded px-2 py-1.5 text-[9px]"
            style={{ backgroundColor: C.blue + "12", color: C.blue, border: `1px solid ${C.blue}22` }}
          >
            Al registrar, se enviara un reporte completo con PDF al email cada vez que se ejecute el analisis,
            ya sea automatico (programado) o manual.
          </div>

          {/* Buttons */}
          <div className="flex gap-1.5">
            <Btn color={C.green} onClick={handleRegister} disabled={submitting || !name.trim() || !email.trim()} full>
              {submitting ? "Registrando..." : "Registrar"}
            </Btn>
            <Btn color={C.blue} onClick={handleTestEmail} disabled={testingEmail || !email.trim()}>
              {testingEmail ? "Enviando..." : "Probar email"}
            </Btn>
          </div>
        </div>
      ) : !aoi && !showForm ? (
        <p className="text-xs py-1" style={{ color: C.text2 }}>
          Dibuja un poligono para registrar monitoreo
        </p>
      ) : null}

      {/* Separator */}
      <div style={{ borderTop: `1px solid ${C.border}` }} />

      {/* Monitored areas list */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <p
            className="text-[10px] font-semibold uppercase tracking-wider"
            style={{ color: C.text2 }}
          >
            Areas monitoreadas ({filteredAreas.length})
          </p>
          <div className="flex items-center gap-1">
            {/* Filter tabs */}
            {areas.length > 0 && (
              <div className="flex gap-0.5">
                {(["all", "active", "inactive"] as const).map((f) => (
                  <button
                    key={f}
                    onClick={() => setFilterActive(f)}
                    className="text-[8px] px-1.5 py-0.5 rounded transition-all"
                    style={{
                      backgroundColor: filterActive === f ? C.blue + "22" : "transparent",
                      color: filterActive === f ? C.blue : C.text2,
                      border: `1px solid ${filterActive === f ? C.blue + "44" : "transparent"}`,
                    }}
                  >
                    {f === "all" ? "Todas" : f === "active" ? "Activas" : "Inactivas"}
                  </button>
                ))}
              </div>
            )}
            {areas.length > 0 && (
              <button
                onClick={fetchAreas}
                className="text-[9px] px-1.5 py-0.5 rounded hover:brightness-125"
                style={{ color: C.blue }}
              >
                Refrescar
              </button>
            )}
          </div>
        </div>

        {loading ? (
          <p className="text-xs py-2" style={{ color: C.text2 }}>
            Cargando...
          </p>
        ) : filteredAreas.length === 0 ? (
          <p className="text-xs py-2" style={{ color: C.text2 }}>
            {areas.length === 0 ? "Sin areas monitoreadas" : "Sin resultados para el filtro seleccionado"}
          </p>
        ) : (
          <div className="space-y-1.5 max-h-[50vh] overflow-y-auto pr-0.5">
            {filteredAreas.map((area) => {
              const areaEngines = parseEngines(area.engines)
              const isAnalyzing = analyzingId === area.id
              const isToggling = togglingId === area.id
              const healthColor = getHealthColor(area)
              const healthLabel = getHealthLabel(area)

              return (
                <div key={area.id}>
                  {/* Area card */}
                  <div
                    className="rounded-md px-2.5 py-2 space-y-1 transition-colors"
                    style={{
                      backgroundColor: C.bgCard,
                      border: `1px solid ${C.border}`,
                      borderLeft: `3px solid ${healthColor}`,
                    }}
                  >
                    {/* Row 1: name + health badge + active toggle */}
                    <div className="flex items-center justify-between gap-1.5">
                      <div className="flex items-center gap-1.5 min-w-0">
                        {/* Health indicator dot */}
                        <span
                          className="w-2 h-2 rounded-full flex-shrink-0"
                          style={{ backgroundColor: healthColor }}
                          title={healthLabel}
                        />
                        <span className="text-xs font-medium truncate" style={{ color: C.text1 }}>
                          {area.name}
                        </span>
                      </div>
                      <div className="flex items-center gap-1 flex-shrink-0">
                        <span
                          className="text-[8px] px-1.5 py-0.5 rounded-full"
                          style={{
                            backgroundColor: healthColor + "22",
                            color: healthColor,
                            border: `1px solid ${healthColor}44`,
                          }}
                        >
                          {healthLabel}
                        </span>
                        <button
                          onClick={() => handleToggle(area)}
                          disabled={isToggling}
                          className="text-[9px] font-semibold px-1.5 py-0.5 rounded-full cursor-pointer hover:brightness-125 transition-all disabled:opacity-50"
                          style={{
                            backgroundColor: area.active ? C.green + "22" : C.red + "22",
                            color: area.active ? C.green : C.red,
                            border: `1px solid ${area.active ? C.green + "44" : C.red + "44"}`,
                          }}
                          title={area.active ? "Click para desactivar" : "Click para activar"}
                        >
                          {isToggling ? "..." : area.active ? "ON" : "OFF"}
                        </button>
                      </div>
                    </div>

                    {/* Row 2: email + interval compact */}
                    <div className="flex items-center justify-between text-[10px]">
                      {area.alert_email && (
                        <span className="truncate" style={{ color: C.text2 }}>
                          {area.alert_email}
                        </span>
                      )}
                      <span className="flex-shrink-0 ml-2" style={{ color: C.text2 }}>
                        c/{formatInterval(area.check_interval_hours)}
                      </span>
                    </div>

                    {/* Row 3: engines tags with proper colors */}
                    {areaEngines.length > 0 && (
                      <div className="flex flex-wrap gap-0.5">
                        {areaEngines.map((e) => {
                          const eColor = ENGINE_COLORS[e] || C.purple
                          return (
                            <span
                              key={e}
                              className="text-[8px] px-1 py-0.5 rounded"
                              style={{
                                backgroundColor: eColor + "18",
                                color: eColor,
                                border: `1px solid ${eColor}33`,
                              }}
                            >
                              {e}
                            </span>
                          )
                        })}
                      </div>
                    )}

                    {/* Row 4: details grid */}
                    <div className="grid grid-cols-2 gap-x-2 gap-y-0.5 text-[10px]">
                      <span style={{ color: C.text2 }}>
                        Umbral: <span style={{ color: C.text1 }}>{area.alert_threshold_ha} ha</span>
                      </span>
                      <span style={{ color: C.text2 }}>
                        Alertas:{" "}
                        <span style={{ color: (area.alert_count ?? 0) > 0 ? C.orange : C.text1 }}>
                          {area.alert_count ?? 0}
                        </span>
                      </span>
                      <span style={{ color: C.text2 }}>
                        Revisado: <span style={{ color: C.text1 }}>{relativeTime(area.last_checked)}</span>
                      </span>
                      <span style={{ color: C.text2 }}>
                        Proxima:{" "}
                        <span style={{ color: C.text1 }}>
                          {nextCheckEstimate(area.last_checked, area.check_interval_hours)}
                        </span>
                      </span>
                      <span style={{ color: C.text2 }} className="col-span-2">
                        Creado: <span style={{ color: C.text1 }}>{formatDate(area.created_at)}</span>
                      </span>
                    </div>

                    {/* Row 5: actions */}
                    <div className="flex items-center gap-1 pt-0.5 flex-wrap">
                      <Btn
                        color={C.blue + "33"}
                        onClick={() => toggleHistory(area.id)}
                        small
                      >
                        <span style={{ color: C.blue }}>
                          {expandedId === area.id ? "Ocultar" : "Historial"}
                        </span>
                      </Btn>
                      <Btn
                        color={C.orange + "33"}
                        onClick={() => handleAnalyzeNow(area)}
                        disabled={isAnalyzing}
                        small
                      >
                        <span style={{ color: C.orange }}>
                          {isAnalyzing ? "Analizando..." : "Analizar ahora"}
                        </span>
                      </Btn>
                      {(area.alert_count ?? 0) > 0 && (
                        <Btn
                          color={C.purple + "33"}
                          onClick={() => handlePurgeAlerts(area)}
                          small
                        >
                          <span style={{ color: C.purple }}>Limpiar</span>
                        </Btn>
                      )}
                      <div className="ml-auto">
                        <Btn
                          color={C.red + "33"}
                          onClick={() => handleDelete(area.id, area.name)}
                          small
                        >
                          <span style={{ color: C.red }}>Eliminar</span>
                        </Btn>
                      </div>
                    </div>

                    {/* Analyzing indicator */}
                    {isAnalyzing && (
                      <div
                        className="rounded px-2 py-1 text-[9px] animate-pulse"
                        style={{
                          backgroundColor: C.orange + "15",
                          border: `1px solid ${C.orange}33`,
                          color: C.orange,
                        }}
                      >
                        Ejecutando analisis... El reporte se enviara automaticamente a {area.alert_email}
                      </div>
                    )}
                  </div>

                  {/* Alert history (expanded) */}
                  {expandedId === area.id && (
                    <div className="mt-1 ml-2 space-y-1">
                      {historyLoading ? (
                        <p className="text-[10px] py-1" style={{ color: C.text2 }}>
                          Cargando historial...
                        </p>
                      ) : history.length === 0 ? (
                        <p className="text-[10px] py-1" style={{ color: C.text2 }}>
                          Sin alertas registradas
                        </p>
                      ) : (
                        <>
                          <div className="flex items-center justify-between">
                            <div className="text-[9px]" style={{ color: C.text2 }}>
                              {history.length} alerta{history.length > 1 ? "s" : ""}
                            </div>
                            <div className="text-[9px]" style={{ color: C.text2 }}>
                              {history.filter((a) => a.email_sent).length} emails enviados
                            </div>
                          </div>
                          {history.map((alert) => {
                            const alertColor = ENGINE_COLORS[alert.alert_type] || C.orange
                            return (
                              <div
                                key={alert.id}
                                className="rounded px-2 py-1.5 flex flex-col gap-0.5"
                                style={{
                                  backgroundColor: C.bgCard,
                                  border: `1px solid ${C.border}`,
                                  borderLeft: `2px solid ${alertColor}`,
                                }}
                              >
                                <div className="flex items-center justify-between gap-1">
                                  <span className="text-[10px] font-medium" style={{ color: alertColor }}>
                                    {alert.alert_type}
                                  </span>
                                  <div className="flex items-center gap-1">
                                    <span
                                      className="text-[9px] px-1.5 py-0.5 rounded-full flex-shrink-0"
                                      style={{
                                        backgroundColor: alert.email_sent ? C.green + "22" : C.text2 + "22",
                                        color: alert.email_sent ? C.green : C.text2,
                                        border: `1px solid ${alert.email_sent ? C.green + "44" : C.text2 + "44"}`,
                                      }}
                                    >
                                      {alert.email_sent ? "Reportado" : "Sin enviar"}
                                    </span>
                                  </div>
                                </div>
                                <div className="flex items-center gap-2">
                                  <span className="text-[10px]" style={{ color: C.text2 }}>
                                    {formatDate(alert.detected_at)}
                                  </span>
                                  {alert.area_ha > 0 && (
                                    <span className="text-[10px] font-medium" style={{ color: C.text1 }}>
                                      {alert.area_ha} ha
                                    </span>
                                  )}
                                </div>
                                {alert.details_json && (
                                  <div className="text-[9px] truncate" style={{ color: C.text2 }}>
                                    {(() => {
                                      try {
                                        const d = JSON.parse(alert.details_json)
                                        return d.detail || ""
                                      } catch {
                                        return ""
                                      }
                                    })()}
                                  </div>
                                )}
                              </div>
                            )
                          })}
                        </>
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* Data sources info */}
      <div
        className="rounded px-2 py-1.5 text-[8px] leading-relaxed"
        style={{ color: C.text2, backgroundColor: C.bgCard, border: `1px solid ${C.border}` }}
      >
        <span className="font-semibold" style={{ color: C.text1 }}>Fuentes de datos:</span>{" "}
        Sentinel-2 (ESA, 10m) | Dynamic World (Google, 10m) | Hansen GFC v1.12 (UMD, 30m) |
        GLAD/RADD (WUR, 10m) | MODIS MCD64A1 (NASA, 500m) | Sentinel-1 SAR (ESA, 10m) |
        WRI Drivers (1km). Actualizacion: tiempo real (S2/DW), semanal (GLAD/RADD), anual (Hansen).
      </div>
    </div>
  )
}
