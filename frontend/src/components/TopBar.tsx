import { useEffect, useState, useCallback } from "react"
import type maplibregl from "maplibre-gl"

const C = {
  bgBase: "#0d1117",
  border: "#30363d",
  text1: "#e6edf3",
  text2: "#8b949e",
  green: "#2ea043",
  blue: "#58a6ff",
  red: "#f85149",
} as const

const BASEMAP_ICONS: { key: string; icon: string; tip: string }[] = [
  { key: "Oscuro (Carto)", icon: "\ud83c\udf19", tip: "Oscuro" },
  { key: "Claro (Carto)", icon: "\u2600\ufe0f", tip: "Claro" },
  { key: "Satelite (Esri)", icon: "\ud83d\udef0", tip: "Satelite" },
  { key: "Hibrido (Esri)", icon: "\ud83c\udf0d", tip: "Hibrido" },
  { key: "Voyager (Carto)", icon: "\ud83d\uddfa", tip: "Voyager" },
]

interface TopBarProps {
  basemap: string
  onBasemapChange: (key: string) => void
  map: maplibregl.Map | null
  isAnalyzing: boolean
  analysisStatus: "idle" | "running" | "completed" | "failed"
}

export default function TopBar({ basemap, onBasemapChange, map, isAnalyzing, analysisStatus }: TopBarProps) {
  const [coords, setCoords] = useState<{ lat: number; lng: number } | null>(null)
  const [zoom, setZoom] = useState(9)

  const handleMouseMove = useCallback((e: maplibregl.MapMouseEvent) => {
    setCoords({ lat: e.lngLat.lat, lng: e.lngLat.lng })
  }, [])

  const handleZoom = useCallback(() => {
    if (map) setZoom(Math.round(map.getZoom() * 10) / 10)
  }, [map])

  useEffect(() => {
    if (!map) return
    map.on("mousemove", handleMouseMove)
    map.on("zoom", handleZoom)
    return () => {
      map.off("mousemove", handleMouseMove)
      map.off("zoom", handleZoom)
    }
  }, [map, handleMouseMove, handleZoom])

  const statusIndicator = () => {
    if (isAnalyzing) return (
      <div className="flex items-center gap-1.5">
        <span className="relative flex h-2 w-2">
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full opacity-75" style={{ backgroundColor: C.blue }} />
          <span className="relative inline-flex rounded-full h-2 w-2" style={{ backgroundColor: C.blue }} />
        </span>
        <span className="text-[11px]" style={{ color: C.blue }}>Analizando</span>
      </div>
    )
    if (analysisStatus === "completed") return (
      <div className="flex items-center gap-1.5">
        <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke={C.green} strokeWidth={2.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
        </svg>
        <span className="text-[11px]" style={{ color: C.green }}>Completado</span>
      </div>
    )
    if (analysisStatus === "failed") return (
      <div className="flex items-center gap-1.5">
        <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke={C.red} strokeWidth={2.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
        </svg>
        <span className="text-[11px]" style={{ color: C.red }}>Error</span>
      </div>
    )
    return null
  }

  return (
    <div className="h-[44px] flex items-center justify-between px-3 gap-3 border-b"
      style={{ backgroundColor: C.bgBase, borderColor: C.border }}>

      {/* Left — Logo */}
      <div className="flex items-center gap-2">
        <img src="/apex_logo.svg" alt="APEX" className="h-6 w-6" />
        <span className="text-xs font-bold" style={{ color: C.green }}>APEX</span>
      </div>

      {/* Center — Basemap buttons */}
      <div className="flex items-center gap-1 rounded-md p-0.5" style={{ backgroundColor: "#161b22" }}>
        {BASEMAP_ICONS.map((b) => (
          <button
            key={b.key}
            onClick={() => onBasemapChange(b.key)}
            title={b.tip}
            className={`w-7 h-7 flex items-center justify-center rounded text-sm transition-all ${
              basemap === b.key ? "shadow-sm" : "opacity-50 hover:opacity-80"
            }`}
            style={basemap === b.key ? { backgroundColor: C.border } : {}}
          >
            {b.icon}
          </button>
        ))}
      </div>

      {/* Right — Status + Coordinates */}
      <div className="flex items-center gap-4">
        {statusIndicator()}
        {coords && (
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-mono" style={{ color: C.text2 }}>
              {coords.lat.toFixed(5)}, {coords.lng.toFixed(5)}
            </span>
            <span className="text-[10px] font-mono px-1.5 py-0.5 rounded" style={{ color: C.text2, backgroundColor: "#161b22" }}>
              z{zoom}
            </span>
            <span className="text-[10px] px-1.5 py-0.5 rounded" style={{ color: C.blue, backgroundColor: "#21262d" }}>
              DW 10m · S2 10m
            </span>
          </div>
        )}
      </div>
    </div>
  )
}
