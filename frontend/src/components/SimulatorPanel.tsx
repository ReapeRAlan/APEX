import { useState } from "react"
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

interface SimulationResult {
  scenario: string
  expected_detections: number
  expected_interdictions: number
  cost_per_detection: number
  coverage_pct: number
  risk_reduction: number
  routes: { inspector_id: string; cells: string[]; distance_km: number }[]
}

interface SimulatorPanelProps {
  token: string
}

export default function SimulatorPanel({ token }: SimulatorPanelProps) {
  const [inspectors, setInspectors] = useState(5)
  const [budget, setBudget] = useState(500000)
  const [horizon, setHorizon] = useState(7)
  const [region, setRegion] = useState("")
  const [result, setResult] = useState<SimulationResult | null>(null)
  const [loading, setLoading] = useState(false)

  const headers = { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }

  const runSimulation = async () => {
    setLoading(true)
    setResult(null)
    try {
      const res = await fetch(`${API_BASE_URL}/api/pomdp/simulate`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          num_inspectors: inspectors,
          budget_mxn: budget,
          horizon_days: horizon,
          region: region || undefined,
        }),
      })
      if (res.ok) setResult(await res.json())
    } catch { /* ignore */ }
    setLoading(false)
  }

  return (
    <div className="h-full flex flex-col" style={{ color: C.text1 }}>
      <div className="p-3 border-b" style={{ borderColor: C.border }}>
        <h3 className="text-xs font-bold" style={{ color: C.yellow }}>
          Simulador POMDP
        </h3>
        <p className="text-[10px] mt-0.5" style={{ color: C.text2 }}>
          Simula escenarios de inspección con diferentes recursos
        </p>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {/* Inputs */}
        <div className="space-y-2">
          <SliderInput
            label="Inspectores disponibles"
            value={inspectors}
            min={1}
            max={20}
            onChange={setInspectors}
          />
          <SliderInput
            label={`Presupuesto (MXN): $${budget.toLocaleString()}`}
            value={budget}
            min={50000}
            max={5000000}
            step={50000}
            onChange={setBudget}
          />
          <SliderInput
            label={`Horizonte: ${horizon} días`}
            value={horizon}
            min={1}
            max={30}
            onChange={setHorizon}
          />
          <div>
            <label className="text-[10px] block mb-1" style={{ color: C.text2 }}>
              Región (opcional)
            </label>
            <select
              value={region}
              onChange={(e) => setRegion(e.target.value)}
              className="w-full rounded px-2 py-1 text-xs outline-none"
              style={{ backgroundColor: C.bgBase, border: `1px solid ${C.border}`, color: C.text1 }}
            >
              <option value="">Nacional</option>
              <option value="sureste">Sureste</option>
              <option value="centro">Centro</option>
              <option value="noroeste">Noroeste</option>
              <option value="noreste">Noreste</option>
              <option value="occidente">Occidente</option>
              <option value="peninsula_yucatan">Península de Yucatán</option>
            </select>
          </div>
        </div>

        <button
          onClick={runSimulation}
          disabled={loading}
          className="w-full rounded py-2 text-xs font-medium transition-opacity disabled:opacity-40"
          style={{ backgroundColor: C.accent, color: "#fff" }}
        >
          {loading ? "Simulando..." : "Ejecutar Simulación"}
        </button>

        {/* Results */}
        {result && (
          <div className="space-y-3 pt-2">
            <div className="grid grid-cols-2 gap-2">
              <MetricCard label="Detecciones esperadas" value={result.expected_detections} color={C.green} />
              <MetricCard label="Interdicciones" value={result.expected_interdictions} color={C.yellow} />
              <MetricCard label="Costo por detección" value={`$${result.cost_per_detection.toLocaleString()}`} color={C.text2} />
              <MetricCard label="Cobertura" value={`${(result.coverage_pct * 100).toFixed(1)}%`} color={C.accent} />
            </div>

            <div
              className="rounded-lg p-2"
              style={{ backgroundColor: C.green + "11", border: `1px solid ${C.green}33` }}
            >
              <p className="text-xs font-medium" style={{ color: C.green }}>
                Reducción de riesgo: {(result.risk_reduction * 100).toFixed(1)}%
              </p>
            </div>

            {/* Routes */}
            {result.routes && result.routes.length > 0 && (
              <div>
                <h4 className="text-xs font-bold mb-2" style={{ color: C.text2 }}>
                  Rutas Óptimas ({result.routes.length} inspectores)
                </h4>
                {result.routes.map((route, i) => (
                  <div
                    key={i}
                    className="flex items-center justify-between px-2 py-1 rounded mb-1 text-xs"
                    style={{ backgroundColor: C.bgBase }}
                  >
                    <span>Inspector {route.inspector_id}</span>
                    <span style={{ color: C.text2 }}>
                      {route.cells.length} celdas · {route.distance_km.toFixed(0)} km
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function SliderInput({
  label, value, min, max, step = 1, onChange,
}: {
  label: string; value: number; min: number; max: number; step?: number
  onChange: (v: number) => void
}) {
  return (
    <div>
      <label className="text-[10px] block mb-1" style={{ color: "#8b949e" }}>
        {label}
      </label>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full h-1 rounded-full appearance-none cursor-pointer"
        style={{ accentColor: "#58a6ff" }}
      />
    </div>
  )
}

function MetricCard({ label, value, color }: { label: string; value: string | number; color: string }) {
  return (
    <div
      className="rounded-lg p-2 text-center"
      style={{ backgroundColor: "#0d1117", border: `1px solid #30363d` }}
    >
      <p className="text-sm font-bold" style={{ color }}>{value}</p>
      <p className="text-[10px] mt-0.5" style={{ color: "#8b949e" }}>{label}</p>
    </div>
  )
}
