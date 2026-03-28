import { useMemo } from "react"

const C = {
  bgCard: "#21262d",
  border: "#30363d",
  text1: "#e6edf3",
  text2: "#8b949e",
  blue: "#58a6ff",
} as const

interface DetectionFeature {
  id: number
  engine: string
  properties: Record<string, any>
  geometry: any
}

interface ValidationPanelProps {
  results: any
  onFlyTo: (lng: number, lat: number) => void
  onHighlight: (sourceId: string, featureId: number | null) => void
}

const ENGINE_LABELS: Record<string, string> = {
  deforestation: "Deforestacion",
  vegetation: "Vegetacion",
  structures: "Estructuras",
  urban_expansion: "Exp. Urbana",
  hansen: "Hansen",
  alerts: "Alertas",
  drivers: "Drivers",
  fire: "Incendios",
  sar: "SAR",
  firms_hotspots: "FIRMS NRT",
}
const ENGINE_COLORS: Record<string, string> = {
  deforestation: "#f85149",
  vegetation: "#2ea043",
  structures: "#58a6ff",
  urban_expansion: "#f0883e",
  hansen: "#facc15",
  alerts: "#dc2626",
  drivers: "#8b5cf6",
  fire: "#f97316",
  sar: "#06b6d4",
  firms_hotspots: "#ff3b30",
}
const SOURCE_PREFIX = "apex-"

function centroid(geometry: any): [number, number] {
  if (!geometry || !geometry.coordinates) return [0, 0]
  const coords =
    geometry.type === "Polygon" ? geometry.coordinates[0] :
    geometry.type === "MultiPolygon" ? geometry.coordinates[0][0] :
    [[0, 0]]
  const n = coords.length
  const lng = coords.reduce((s: number, c: number[]) => s + c[0], 0) / n
  const lat = coords.reduce((s: number, c: number[]) => s + c[1], 0) / n
  return [lng, lat]
}

export default function ValidationPanel({ results, onFlyTo, onHighlight }: ValidationPanelProps) {
  const features = useMemo<DetectionFeature[]>(() => {
    if (!results?.layers) return []
    const list: DetectionFeature[] = []
    let id = 0
    for (const engine of ["deforestation", "vegetation", "structures", "urban_expansion", "hansen", "alerts", "drivers", "fire", "sar", "firms_hotspots"] as const) {
      const layer = results.layers[engine]
      if (!layer?.geojson?.features) continue
      for (const f of layer.geojson.features) {
        list.push({ id: id++, engine, properties: f.properties ?? {}, geometry: f.geometry })
      }
    }
    return list
  }, [results])

  if (features.length === 0) return null

  return (
    <div className="space-y-1.5">
      <p className="text-xs mb-1" style={{ color: C.text2 }}>{features.length} detecciones</p>
      <div className="max-h-[50vh] overflow-y-auto space-y-1 pr-1">
        {features.map((f) => {
          const [lng, lat] = centroid(f.geometry)
          const engineColor = ENGINE_COLORS[f.engine]
          const label = f.engine === "vegetation"
            ? (f.properties.class ?? "?")
            : f.engine === "deforestation"
            ? `${f.properties.area_ha ?? "?"} ha \u2192 ${f.properties.transition_to ?? ""}`
            : f.engine === "urban_expansion"
            ? `${f.properties.area_ha ?? "?"} ha ${f.properties.from_class ?? ""} \u2192 ${f.properties.to_class ?? ""}`
            : (f.properties.note ?? f.properties.type ?? "?")
          return (
            <div
              key={f.id}
              className="flex items-center gap-2 rounded-md px-2.5 py-2 cursor-pointer transition-colors hover:brightness-125"
              style={{ backgroundColor: C.bgCard, border: `1px solid ${C.border}` }}
              onMouseEnter={() => onHighlight(SOURCE_PREFIX + f.engine, f.id)}
              onMouseLeave={() => onHighlight(SOURCE_PREFIX + f.engine, null)}
              onClick={() => onFlyTo(lng, lat)}
            >
              <span className="text-[10px] font-bold uppercase" style={{ color: engineColor }}>
                {ENGINE_LABELS[f.engine]?.slice(0, 3)}
              </span>
              <span className="text-xs flex-1 truncate" style={{ color: C.text1 }}>{label}</span>
              <button
                className="text-[10px] whitespace-nowrap hover:brightness-125 transition-colors"
                style={{ color: C.blue }}
                onClick={(e) => { e.stopPropagation(); onFlyTo(lng, lat) }}
              >
                Centrar
              </button>
            </div>
          )
        })}
      </div>
    </div>
  )
}