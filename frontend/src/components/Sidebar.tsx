import { useState, type ReactNode } from "react"
import JobStatus from "./JobStatus"
import StatsCard from "./StatsCard"
import TimelinePanel from "./TimelinePanel"
import MonitoringPanel from "./MonitoringPanel"
import ChatPanel from "./ChatPanel"
import PolygonManager, { type UploadedPolygon } from "./PolygonManager"
import type { DrawMode } from "./MapView"
import { API_BASE_URL } from "../config"

/* ── Design tokens ────────────────────────────── */
const C = {
  bgBase: "#0d1117",
  bgPanel: "#161b22",
  bgCard: "#21262d",
  border: "#30363d",
  green: "#2ea043",
  orange: "#f0883e",
  red: "#f85149",
  text1: "#e6edf3",
  text2: "#8b949e",
  blue: "#58a6ff",
} as const

const ENGINE_META: Record<string, { label: string; color: string; accent: string }> = {
  deforestation: { label: "Deforestacion (DW)", color: "#f85149", accent: "bg-red-500/20 text-red-400 border-red-500/30" },
  vegetation: { label: "Vegetacion (DW)", color: "#2ea043", accent: "bg-green-500/20 text-green-400 border-green-500/30" },
  structures: { label: "Estructuras", color: "#58a6ff", accent: "bg-blue-500/20 text-blue-400 border-blue-500/30" },
  urban_expansion: { label: "Expansion urbana", color: "#f0883e", accent: "bg-orange-500/20 text-orange-400 border-orange-500/30" },
  hansen: { label: "Hansen Forest Loss", color: "#facc15", accent: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30" },
  alerts: { label: "Alertas GLAD/RADD", color: "#dc2626", accent: "bg-red-600/20 text-red-300 border-red-600/30" },
  drivers: { label: "Drivers (WRI)", color: "#8b5cf6", accent: "bg-purple-500/20 text-purple-400 border-purple-500/30" },
  fire: { label: "Incendios (MODIS)", color: "#f97316", accent: "bg-orange-600/20 text-orange-300 border-orange-600/30" },
  sar: { label: "SAR (Sentinel-1)", color: "#06b6d4", accent: "bg-cyan-500/20 text-cyan-400 border-cyan-500/30" },
  firms_hotspots: { label: "FIRMS NRT (hotspots)", color: "#ff3b30", accent: "bg-red-600/20 text-red-300 border-red-600/30" },
  avocado: { label: "Anomalías NDVI (AVOCADO)", color: "#a855f7", accent: "bg-violet-500/20 text-violet-400 border-violet-500/30" },
  spectralgpt: { label: "SpectralGPT (LULC)", color: "#14b8a6", accent: "bg-teal-500/20 text-teal-400 border-teal-500/30" },
  drivers_mx: { label: "ForestNet-MX (Drivers)", color: "#c084fc", accent: "bg-purple-400/20 text-purple-300 border-purple-400/30" },
}

const ALL_ENGINE_KEYS = Object.keys(ENGINE_META) as (keyof typeof ENGINE_META)[]

const ENGINE_GROUPS: { label: string; color: string; engines: string[] }[] = [
  { label: "Detección Base", color: "#58a6ff", engines: ["deforestation", "vegetation", "structures", "urban_expansion"] },
  { label: "Pérdida Forestal", color: "#facc15", engines: ["hansen", "alerts", "drivers", "drivers_mx"] },
  { label: "IA / Sensores", color: "#14b8a6", engines: ["spectralgpt", "sar", "avocado"] },
  { label: "Incendios", color: "#f97316", engines: ["fire", "firms_hotspots"] },
]

type Tab = "config" | "results" | "monitoring"

interface SidebarProps {
  aoi: object | null
  engines: string[]
  onToggleEngine: (engine: string) => void
  onSetAllEngines: () => void
  onClearEngines: () => void
  onAnalyze: (startYear: number, endYear: number, season: string) => void
  isAnalyzing: boolean
  jobId: string | null
  timelineJobId: string | null
  results: any | null
  onJobCompleted?: (id: string) => void
  layerVis: Record<string, boolean>
  onToggleLayer: (key: string, visible: boolean) => void
  onShowAllLayers: () => void
  onHideAllLayers: () => void
  onRenderYear: (year: number, data: any) => void
  onClearYearLayers?: () => void
  onClearAoi?: () => void
  onEditAoi?: () => void
  onStartDraw?: () => void
  onCancelDraw?: () => void
  drawMode?: DrawMode
  notifyEmail: string
  onNotifyEmailChange: (email: string) => void
  uploadedPolygons: UploadedPolygon[]
  onUploadedPolygonsChange: (polygons: UploadedPolygon[]) => void
  onUsePolygonAsAoi: (geometry: any) => void
  onFlyToBbox: (bbox: number[]) => void
}

/* ── Collapsible Section ──────────────────────── */
function Section({ title, children, defaultOpen = true }: { title: string; children: ReactNode; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="border-b" style={{ borderColor: C.border }}>
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-4 py-2.5 text-xs font-semibold uppercase tracking-wider hover:bg-white/5 transition-colors"
        style={{ color: C.text2 }}
      >
        {title}
        <svg className={`w-3.5 h-3.5 transition-transform ${open ? "rotate-180" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {open && <div className="px-4 pb-3">{children}</div>}
    </div>
  )
}

/* ── Layer toggle row ─────────────────────────── */
function LayerToggle({ label, color, checked, onChange }: { label: string; color: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-center gap-2.5 py-1 cursor-pointer group">
      <span className="relative flex items-center justify-center w-4 h-4 rounded border transition-colors"
        style={{ borderColor: checked ? color : C.border, backgroundColor: checked ? color + "33" : "transparent" }}>
        {checked && (
          <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke={color} strokeWidth={3}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
          </svg>
        )}
        <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} className="absolute inset-0 opacity-0 cursor-pointer" />
      </span>
      <span className="text-xs group-hover:brightness-125 transition-all" style={{ color }}>{label}</span>
    </label>
  )
}

/* ── Collapsible engine group ─────────────────── */
function EngineGroup({ label, color, activeCount, total, children }: {
  label: string; color: string; activeCount: number; total: number; children: ReactNode
}) {
  const [open, setOpen] = useState(true)
  return (
    <div className="mb-1.5">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-2 py-1.5 rounded-md text-[11px] font-semibold transition-colors hover:bg-white/5"
        style={{ color }}
      >
        <span className="flex items-center gap-1.5">
          <svg className={`w-3 h-3 transition-transform ${open ? "rotate-90" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
          </svg>
          {label}
        </span>
        <span className="text-[10px] font-normal px-1.5 py-0.5 rounded-full" style={{ backgroundColor: color + "22", color }}>
          {activeCount}/{total}
        </span>
      </button>
      {open && <div className="ml-2 space-y-0.5">{children}</div>}
    </div>
  )
}

/* ── Send Report by Email (in Results tab) ──── */
function SendReportSection({ jobId, notifyEmail }: { jobId: string | null; notifyEmail: string }) {
  const [email, setEmail] = useState(notifyEmail)
  const [sending, setSending] = useState(false)
  const [status, setStatus] = useState<{ text: string; ok: boolean } | null>(null)

  const handleSend = async () => {
    if (!jobId || !email.trim()) return
    setSending(true)
    setStatus(null)
    try {
      const res = await fetch(`${API_BASE_URL}/api/results/${jobId}/send-report`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim() }),
      })
      if (res.ok) {
        const data = await res.json()
        setStatus({ text: `Reporte enviado a ${email} (${data.folio})`, ok: true })
      } else {
        const err = await res.json().catch(() => ({ detail: "Error desconocido" }))
        setStatus({ text: typeof err.detail === "string" ? err.detail : "Error al enviar", ok: false })
      }
    } catch {
      setStatus({ text: "Error de red", ok: false })
    } finally {
      setSending(false)
      setTimeout(() => setStatus(null), 5000)
    }
  }

  return (
    <div className="mt-3 pt-3 space-y-1.5" style={{ borderTop: `1px solid ${C.border}` }}>
      <p className="text-[10px] font-semibold uppercase tracking-wider" style={{ color: C.text2 }}>
        Enviar reporte por email
      </p>
      <input
        type="email"
        placeholder="inspector@profepa.gob.mx"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        className="w-full rounded-md px-2.5 py-1.5 text-xs outline-none placeholder:text-gray-500"
        style={{ backgroundColor: C.bgCard, border: `1px solid ${C.border}`, color: C.text1 }}
      />
      <button
        onClick={handleSend}
        disabled={sending || !email.trim() || !jobId}
        className="w-full py-1.5 rounded-md text-xs font-semibold transition-all disabled:opacity-30 disabled:cursor-not-allowed hover:brightness-110"
        style={{ backgroundColor: C.blue, color: "#fff" }}
      >
        {sending ? "Enviando reporte..." : "Enviar reporte con PDF"}
      </button>
      {status && (
        <div
          className="rounded-md px-2 py-1.5 text-[10px] font-medium"
          style={{
            backgroundColor: status.ok ? C.green + "22" : C.red + "22",
            color: status.ok ? C.green : C.red,
            border: `1px solid ${status.ok ? C.green + "44" : C.red + "44"}`,
          }}
        >
          {status.text}
        </div>
      )}
    </div>
  )
}

/* ── Main Sidebar ─────────────────────────────── */
export default function Sidebar({
  aoi, engines, onToggleEngine, onSetAllEngines, onClearEngines, onAnalyze,
  isAnalyzing, jobId, timelineJobId, results, onJobCompleted,
  layerVis, onToggleLayer, onShowAllLayers, onHideAllLayers, onRenderYear, onClearYearLayers,
  onClearAoi, onEditAoi, onStartDraw, onCancelDraw,
  drawMode, notifyEmail, onNotifyEmailChange,
  uploadedPolygons, onUploadedPolygonsChange, onUsePolygonAsAoi, onFlyToBbox,
}: SidebarProps) {
  const [activeTab, setActiveTab] = useState<Tab>("config")
  const [timelineStartYear, setTimelineStartYear] = useState(2018)
  const [timelineEndYear, setTimelineEndYear] = useState(2025)
  const [timelineSeason, setTimelineSeason] = useState("dry")

  const handleTabChange = (tab: Tab) => {
    if (activeTab === "results" && tab !== "results") {
      onClearYearLayers?.()
    }
    setActiveTab(tab)
  }

  // Auto-switch to results when analysis completes
  const prevResults = useState(results)[0]
  if (results && !prevResults && activeTab === "config") {
    // Don't auto-switch — let user decide
  }

  const tabs: { key: Tab; label: string; icon: string }[] = [
    { key: "config", label: "Configurar", icon: "⚙" },
    { key: "results", label: "Resultados", icon: "📊" },
    { key: "monitoring", label: "Monitoreo", icon: "🛰" },
  ]

  return (
    <div className="flex flex-col h-full w-[300px] select-none" style={{ backgroundColor: C.bgPanel }}>
      {/* ─── Header ─── */}
      <div className="flex-shrink-0 px-4 pt-3 pb-2" style={{ background: `linear-gradient(180deg, ${C.bgPanel} 0%, ${C.bgBase} 100%)` }}>
        <div className="flex items-center gap-3">
          <img src="/apex_logo.svg" alt="APEX" className="h-10 w-10" />
          <div>
            <h1 className="text-base font-bold tracking-tight" style={{ color: C.text1 }}>APEX</h1>
            <p className="text-[10px] leading-tight" style={{ color: C.text2 }}>
              Analisis Predictivo de Ecosistemas con IA
            </p>
          </div>
        </div>
      </div>

      {/* ─── Tabs ─── */}
      <div className="flex-shrink-0 flex border-b" style={{ borderColor: C.border }}>
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => handleTabChange(t.key)}
            className={`flex-1 py-2 text-xs font-medium transition-colors relative ${
              activeTab === t.key ? "text-white" : "hover:text-white/80"
            }`}
            style={{ color: activeTab === t.key ? C.text1 : C.text2 }}
          >
            <span className="flex items-center justify-center gap-1">
              <span className="text-[10px]">{t.icon}</span>
              {t.label}
            </span>
            {activeTab === t.key && (
              <span className="absolute bottom-0 left-2 right-2 h-0.5 rounded-full" style={{ backgroundColor: C.green }} />
            )}
            {/* Badge for results */}
            {t.key === "results" && results && (
              <span className="absolute top-1 right-1 w-1.5 h-1.5 rounded-full" style={{ backgroundColor: C.green }} />
            )}
          </button>
        ))}
      </div>

      {/* ─── Scrollable content ─── */}
      <div className="flex-1 overflow-y-auto overflow-x-hidden">

        {/* ══════════ CONFIG TAB ══════════ */}
        {activeTab === "config" && (
          <>
            {/* ── 1. AOI: Draw / Upload ── */}
            <Section title="1. Area de interes (AOI)">
              {aoi ? (
                <div className="space-y-2">
                  <div className="flex items-center gap-2 px-2 py-1.5 rounded-md" style={{ backgroundColor: C.green + "15", border: `1px solid ${C.green}33` }}>
                    <span className="w-2 h-2 rounded-full" style={{ backgroundColor: C.green }} />
                    <span className="text-[11px] font-medium" style={{ color: C.green }}>AOI definido</span>
                  </div>
                  <div className="flex gap-2">
                    <button
                      onClick={onEditAoi}
                      className="flex-1 px-2 py-1.5 rounded-md text-xs font-medium border transition-colors"
                      style={{
                        borderColor: drawMode === "select" ? C.blue : C.border,
                        color: drawMode === "select" ? C.blue : C.text2,
                        backgroundColor: drawMode === "select" ? C.blue + "22" : "transparent",
                      }}
                    >
                      {drawMode === "select" ? "Editando..." : "Editar"}
                    </button>
                    <button
                      onClick={onClearAoi}
                      className="flex-1 px-2 py-1.5 rounded-md text-xs font-medium border transition-colors hover:bg-red-500/10"
                      style={{ borderColor: C.red + "44", color: C.red }}
                    >
                      Borrar
                    </button>
                  </div>
                </div>
              ) : drawMode === "polygon" ? (
                <div className="space-y-1.5">
                  <div className="flex items-center gap-2 px-2 py-1.5 rounded-md" style={{ backgroundColor: C.blue + "22", border: `1px solid ${C.blue}44` }}>
                    <span className="relative flex h-2 w-2">
                      <span className="animate-ping absolute inline-flex h-full w-full rounded-full opacity-75" style={{ backgroundColor: C.blue }} />
                      <span className="relative inline-flex rounded-full h-2 w-2" style={{ backgroundColor: C.blue }} />
                    </span>
                    <span className="text-[11px] font-medium" style={{ color: C.blue }}>Dibujando poligono...</span>
                  </div>
                  <p className="text-[10px]" style={{ color: C.text2 }}>
                    Haz clic en el mapa para agregar vertices. Cierra el poligono haciendo clic en el primer punto.
                  </p>
                  <button
                    onClick={onCancelDraw}
                    className="w-full py-1.5 rounded-md text-xs font-medium border transition-colors hover:bg-red-500/10"
                    style={{ borderColor: C.red + "66", color: C.red }}
                  >
                    Cancelar
                  </button>
                </div>
              ) : (
                <div className="space-y-2">
                  <button
                    onClick={onStartDraw}
                    className="w-full py-2 rounded-md text-xs font-semibold border transition-all hover:brightness-110"
                    style={{ borderColor: C.blue + "66", color: C.blue, backgroundColor: C.blue + "15" }}
                  >
                    <span className="flex items-center justify-center gap-1.5">
                      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                      </svg>
                      Dibujar poligono en mapa
                    </span>
                  </button>
                  <PolygonManager
                    polygons={uploadedPolygons}
                    onPolygonsChange={onUploadedPolygonsChange}
                    onUseAsAoi={onUsePolygonAsAoi}
                    onFlyToBbox={onFlyToBbox}
                  />
                </div>
              )}
            </Section>

            {/* ── 2. Engines ── */}
            <Section title="2. Motores de deteccion">
              <div className="flex gap-1.5 mb-2">
                <button
                  onClick={onSetAllEngines}
                  className="flex-1 px-2 py-1 rounded text-[10px] font-semibold border transition-colors hover:bg-white/10"
                  style={{ borderColor: C.border, color: C.text2 }}
                >Todos ({ALL_ENGINE_KEYS.length})</button>
                <button
                  onClick={onClearEngines}
                  className="flex-1 px-2 py-1 rounded text-[10px] font-semibold border transition-colors hover:bg-white/10"
                  style={{ borderColor: C.border, color: C.text2 }}
                >Ninguno</button>
              </div>
              <p className="text-[10px] mb-2" style={{ color: C.text2 }}>
                {engines.length} de {ALL_ENGINE_KEYS.length} seleccionados
              </p>
              {ENGINE_GROUPS.map((group) => {
                const activeCount = group.engines.filter((e) => engines.includes(e)).length
                return (
                  <EngineGroup key={group.label} label={group.label} color={group.color} activeCount={activeCount} total={group.engines.length}>
                    {group.engines.map((e) => {
                      const meta = ENGINE_META[e]
                      if (!meta) return null
                      const active = engines.includes(e)
                      return (
                        <button
                          key={e}
                          onClick={() => onToggleEngine(e)}
                          className={`w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded-md border text-xs font-medium transition-all ${
                            active ? meta.accent : "border-transparent text-gray-500 hover:bg-white/5"
                          }`}
                          style={{ borderColor: active ? undefined : "transparent" }}
                        >
                          <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: active ? meta.color : C.border }} />
                          {meta.label}
                        </button>
                      )
                    })}
                  </EngineGroup>
                )
              })}
            </Section>

            {/* ── 3. Timeline config ── */}
            <Section title="3. Periodo temporal" defaultOpen={true}>
              <div className="space-y-2">
                <div className="flex gap-2 items-center">
                  <select
                    value={timelineStartYear}
                    onChange={(e) => setTimelineStartYear(Number(e.target.value))}
                    className="flex-1 rounded-md px-2 py-1.5 text-xs outline-none"
                    style={{ backgroundColor: C.bgCard, border: `1px solid ${C.border}`, color: C.text1 }}
                  >
                    {Array.from({ length: 10 }, (_, i) => 2016 + i).map((y) => (
                      <option key={y} value={y}>{y}</option>
                    ))}
                  </select>
                  <span className="text-xs" style={{ color: C.text2 }}>→</span>
                  <select
                    value={timelineEndYear}
                    onChange={(e) => setTimelineEndYear(Number(e.target.value))}
                    className="flex-1 rounded-md px-2 py-1.5 text-xs outline-none"
                    style={{ backgroundColor: C.bgCard, border: `1px solid ${C.border}`, color: C.text1 }}
                  >
                    {Array.from({ length: 10 }, (_, i) => 2016 + i).map((y) => (
                      <option key={y} value={y}>{y}</option>
                    ))}
                  </select>
                </div>
                <div className="flex gap-1.5">
                  {([["dry", "Seca"], ["wet", "Lluviosa"], ["annual", "Anual"]] as const).map(([val, lbl]) => (
                    <button
                      key={val}
                      onClick={() => setTimelineSeason(val)}
                      className={`flex-1 px-2 py-1.5 rounded-md text-[10px] font-medium border transition-all ${
                        timelineSeason === val ? "border-orange-500/40 text-orange-400 bg-orange-500/15" : "border-transparent text-gray-500 hover:bg-white/5"
                      }`}
                    >{lbl}</button>
                  ))}
                </div>
                <p className="text-[10px] text-center" style={{ color: C.text2 }}>
                  {engines.length} motores × {timelineEndYear - timelineStartYear + 1} anos = analisis completo + timeline
                </p>
              </div>
            </Section>

            {/* ── 4. Email (collapsed) ── */}
            <Section title="Notificacion por email" defaultOpen={false}>
              <div className="space-y-1.5">
                <input
                  type="email"
                  placeholder="inspector@profepa.gob.mx"
                  value={notifyEmail}
                  onChange={(e) => onNotifyEmailChange(e.target.value)}
                  className="w-full rounded-md px-2.5 py-1.5 text-xs outline-none placeholder:text-gray-500"
                  style={{ backgroundColor: C.bgCard, border: `1px solid ${C.border}`, color: C.text1 }}
                />
                {notifyEmail && (
                  <div className="flex items-center gap-1.5">
                    <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: C.green }} />
                    <span className="text-[10px]" style={{ color: C.green }}>Se enviara reporte a {notifyEmail}</span>
                  </div>
                )}
              </div>
            </Section>

            {/* ── Job progress (appears when running) ── */}
            {jobId && (
              <div className="px-4 py-3 border-b" style={{ borderColor: C.border }}>
                <JobStatus jobId={jobId} onCompleted={onJobCompleted} />
              </div>
            )}
          </>
        )}

        {/* ══════════ RESULTS TAB ══════════ */}
        {activeTab === "results" && (
          <>
            {results ? (
              <>
                {/* Stats */}
                <div className="px-4 py-3 border-b" style={{ borderColor: C.border }}>
                  <StatsCard results={results} />
                  <SendReportSection jobId={jobId} notifyEmail={notifyEmail} />
                </div>

                {/* Timeline viewer */}
                {timelineJobId && (
                  <Section title="Timeline multi-temporal">
                    <TimelinePanel jobId={timelineJobId} onYearSelect={onRenderYear} />
                  </Section>
                )}

                {/* Layer management */}
                <Section title="Capas del mapa">
                  <div className="flex gap-1.5 mb-2">
                    <button
                      onClick={onShowAllLayers}
                      className="flex-1 px-2 py-1 rounded text-[10px] font-semibold border transition-colors hover:bg-white/10"
                      style={{ borderColor: C.border, color: C.text2 }}
                    >Mostrar todo</button>
                    <button
                      onClick={onHideAllLayers}
                      className="flex-1 px-2 py-1 rounded text-[10px] font-semibold border transition-colors hover:bg-white/10"
                      style={{ borderColor: C.border, color: C.text2 }}
                    >Ocultar todo</button>
                  </div>
                  <LayerToggle label="Deforestacion" color="#f85149" checked={layerVis.def ?? false} onChange={(v) => onToggleLayer("def", v)} />
                  <LayerToggle label="Estructuras" color="#58a6ff" checked={layerVis.str ?? false} onChange={(v) => onToggleLayer("str", v)} />
                  <LayerToggle label="Vegetacion" color="#2ea043" checked={layerVis.veg ?? false} onChange={(v) => onToggleLayer("veg", v)} />
                  <LayerToggle label="Expansion urbana" color="#f0883e" checked={layerVis.ue ?? false} onChange={(v) => onToggleLayer("ue", v)} />
                  <LayerToggle label="Hansen Forest Loss" color="#facc15" checked={layerVis.hansen ?? false} onChange={(v) => onToggleLayer("hansen", v)} />
                  <LayerToggle label="Alertas GLAD/RADD" color="#dc2626" checked={layerVis.alerts ?? false} onChange={(v) => onToggleLayer("alerts", v)} />
                  <LayerToggle label="Drivers (WRI)" color="#8b5cf6" checked={layerVis.drivers ?? false} onChange={(v) => onToggleLayer("drivers", v)} />
                  <LayerToggle label="ForestNet-MX" color="#c084fc" checked={layerVis.drivers_mx ?? false} onChange={(v) => onToggleLayer("drivers_mx", v)} />
                  <LayerToggle label="Incendios" color="#f97316" checked={layerVis.fire ?? false} onChange={(v) => onToggleLayer("fire", v)} />
                  <LayerToggle label="FIRMS Hotspots" color="#ff3b30" checked={layerVis.firms_hotspots ?? false} onChange={(v) => onToggleLayer("firms_hotspots", v)} />
                  <LayerToggle label="ANPs" color="#22c55e" checked={layerVis.anp ?? false} onChange={(v) => onToggleLayer("anp", v)} />
                  <LayerToggle label="SAR (Sentinel-1)" color="#06b6d4" checked={layerVis.sar ?? false} onChange={(v) => onToggleLayer("sar", v)} />
                  <LayerToggle label="AVOCADO (NDVI)" color="#a855f7" checked={layerVis.avocado ?? false} onChange={(v) => onToggleLayer("avocado", v)} />
                  <LayerToggle label="SpectralGPT" color="#14b8a6" checked={layerVis.spectralgpt ?? false} onChange={(v) => onToggleLayer("spectralgpt", v)} />
                </Section>

                {/* Chat IA */}
                <Section title="Chat IA" defaultOpen={false}>
                  <div style={{ height: 350 }}>
                    <ChatPanel jobId={jobId} results={results} />
                  </div>
                </Section>
              </>
            ) : (
              <div className="text-center py-12 px-4">
                <div className="text-2xl mb-3 opacity-30">📊</div>
                <p className="text-xs mb-1" style={{ color: C.text1 }}>Sin resultados</p>
                <p className="text-[10px]" style={{ color: C.text2 }}>
                  Configura tus motores y AOI en la pestana Configurar, luego presiona "Analizar AOI".
                </p>
              </div>
            )}
          </>
        )}

        {/* ══════════ MONITORING TAB ══════════ */}
        <div style={{ display: activeTab === "monitoring" ? undefined : "none" }} className="px-4 py-3">
          <MonitoringPanel aoi={aoi} />
        </div>
      </div>

      {/* ─── Fixed bottom: single unified action ─── */}
      <div className="flex-shrink-0 p-3 border-t space-y-2" style={{ borderColor: C.border, backgroundColor: C.bgBase }}>
        {isAnalyzing && (
          <div className="flex items-center gap-2 px-2 py-1.5 rounded-md" style={{ backgroundColor: C.bgCard }}>
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full opacity-75" style={{ backgroundColor: C.blue }} />
              <span className="relative inline-flex rounded-full h-2 w-2" style={{ backgroundColor: C.blue }} />
            </span>
            <span className="text-[11px]" style={{ color: C.text2 }}>Analizando {engines.length} motores × {timelineEndYear - timelineStartYear + 1} anos...</span>
          </div>
        )}

        <button
          onClick={() => onAnalyze(timelineStartYear, timelineEndYear, timelineSeason)}
          disabled={!aoi || isAnalyzing}
          className="w-full py-2.5 rounded-lg text-sm font-bold transition-all disabled:opacity-30 disabled:cursor-not-allowed hover:brightness-110 active:scale-[0.98]"
          style={{ backgroundColor: C.green, color: "#fff" }}
        >
          {!aoi
            ? "Dibuja un poligono para iniciar"
            : isAnalyzing
            ? "Analizando..."
            : `Analizar AOI (${engines.length} motores)`}
        </button>

        {!aoi && !isAnalyzing && (
          <p className="text-[10px] text-center" style={{ color: C.text2 }}>
            Paso 1: Dibuja o sube un poligono en el mapa
          </p>
        )}
      </div>
    </div>
  )
}
