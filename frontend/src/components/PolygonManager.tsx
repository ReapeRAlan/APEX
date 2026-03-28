import { useCallback, useRef, useState } from "react"
import { API_BASE_URL } from "../config"

/* ── Design tokens ─── */
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
  cyan: "#22d3ee",
} as const

const PALETTE = ["#22d3ee", "#f59e0b", "#a78bfa", "#34d399", "#f472b6", "#60a5fa", "#fb923c", "#c084fc"]

const ACCEPTED_FORMATS = ".geojson,.json,.kml,.kmz,.gpx,.zip,.wkt"

export interface UploadedPolygon {
  id: string
  name: string
  filename: string
  format: string
  polygon_count: number
  polygons: any[]
  feature_collection: any
  bbox: number[] | null
  color: string
  visible: boolean
}

interface PolygonManagerProps {
  polygons: UploadedPolygon[]
  onPolygonsChange: (polygons: UploadedPolygon[]) => void
  onUseAsAoi: (geometry: any) => void
  onFlyToBbox: (bbox: number[]) => void
}

export default function PolygonManager({ polygons, onPolygonsChange, onUseAsAoi, onFlyToBbox }: PolygonManagerProps) {
  const [uploading, setUploading] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [wktMode, setWktMode] = useState(false)
  const [wktText, setWktText] = useState("")
  const fileInputRef = useRef<HTMLInputElement>(null)

  const nextColor = useCallback(() => {
    return PALETTE[polygons.length % PALETTE.length]
  }, [polygons.length])

  const handleUpload = useCallback(async (files: FileList | null) => {
    if (!files || files.length === 0) return
    setError(null)
    setUploading(true)

    const results: UploadedPolygon[] = []

    for (let i = 0; i < files.length; i++) {
      const file = files[i]
      const formData = new FormData()
      formData.append("file", file)

      try {
        const res = await fetch(`${API_BASE_URL}/api/polygons/upload`, {
          method: "POST",
          body: formData,
        })

        if (!res.ok) {
          const err = await res.json().catch(() => ({ detail: `Error ${res.status}` }))
          throw new Error(typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail))
        }

        const data = await res.json()
        results.push({
          ...data,
          color: PALETTE[(polygons.length + results.length) % PALETTE.length],
          visible: true,
        })
      } catch (e: any) {
        setError(`${file.name}: ${e.message}`)
      }
    }

    if (results.length > 0) {
      onPolygonsChange([...polygons, ...results])
    }
    setUploading(false)
  }, [polygons, onPolygonsChange])

  const handleWktSubmit = useCallback(async () => {
    if (!wktText.trim()) return
    setError(null)
    setUploading(true)

    try {
      const res = await fetch(`${API_BASE_URL}/api/polygons/parse-wkt`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ wkt: wktText.trim() }),
      })

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: `Error ${res.status}` }))
        throw new Error(typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail))
      }

      const data = await res.json()
      onPolygonsChange([...polygons, { ...data, filename: "WKT", color: nextColor(), visible: true }])
      setWktText("")
      setWktMode(false)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setUploading(false)
    }
  }, [wktText, polygons, onPolygonsChange, nextColor])

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    handleUpload(e.dataTransfer.files)
  }, [handleUpload])

  const handleRemove = (id: string) => {
    onPolygonsChange(polygons.filter((p) => p.id !== id))
  }

  const handleToggleVisibility = (id: string) => {
    onPolygonsChange(polygons.map((p) => p.id === id ? { ...p, visible: !p.visible } : p))
  }

  const handleUseAsAoi = (poly: UploadedPolygon) => {
    // Use first polygon as AOI
    if (poly.polygons.length > 0) {
      onUseAsAoi(poly.polygons[0])
    }
  }

  const handleZoomTo = (poly: UploadedPolygon) => {
    if (poly.bbox) {
      onFlyToBbox(poly.bbox)
    }
  }

  return (
    <div className="space-y-2">
      {/* Upload area */}
      <div
        className="relative rounded-lg border-2 border-dashed transition-colors cursor-pointer"
        style={{
          borderColor: dragOver ? C.cyan : C.border,
          backgroundColor: dragOver ? C.cyan + "11" : "transparent",
        }}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        onClick={() => fileInputRef.current?.click()}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept={ACCEPTED_FORMATS}
          multiple
          className="hidden"
          onChange={(e) => handleUpload(e.target.files)}
        />
        <div className="flex flex-col items-center py-4 px-2 text-center">
          {uploading ? (
            <div className="flex items-center gap-2">
              <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke={C.cyan} strokeWidth="4" />
                <path className="opacity-75" fill={C.cyan} d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
              <span className="text-xs" style={{ color: C.cyan }}>Procesando...</span>
            </div>
          ) : (
            <>
              <svg className="w-6 h-6 mb-1" fill="none" viewBox="0 0 24 24" stroke={C.text2} strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" />
              </svg>
              <span className="text-[10px] font-medium" style={{ color: C.text2 }}>
                Arrastra archivos o haz clic
              </span>
              <span className="text-[9px] mt-0.5" style={{ color: C.text2 + "99" }}>
                GeoJSON, KML, KMZ, GPX, SHP (ZIP), WKT
              </span>
            </>
          )}
        </div>
      </div>

      {/* WKT toggle */}
      <button
        onClick={() => setWktMode(!wktMode)}
        className="w-full text-[10px] font-medium py-1 rounded transition-colors hover:bg-white/5"
        style={{ color: wktMode ? C.cyan : C.text2 }}
      >
        {wktMode ? "Cerrar entrada WKT" : "Pegar WKT manualmente"}
      </button>

      {/* WKT input */}
      {wktMode && (
        <div className="space-y-1.5">
          <textarea
            value={wktText}
            onChange={(e) => setWktText(e.target.value)}
            placeholder="POLYGON((-99.1 19.4, -99.0 19.4, -99.0 19.3, -99.1 19.3, -99.1 19.4))"
            className="w-full rounded-md px-2.5 py-1.5 text-[10px] outline-none resize-y placeholder:text-gray-600 font-mono"
            style={{ backgroundColor: C.bgCard, border: `1px solid ${C.border}`, color: C.text1, minHeight: "60px" }}
          />
          <button
            onClick={handleWktSubmit}
            disabled={uploading || !wktText.trim()}
            className="w-full py-1.5 rounded-md text-xs font-semibold transition-all disabled:opacity-30 disabled:cursor-not-allowed"
            style={{ backgroundColor: C.cyan, color: "#0d1117" }}
          >
            Cargar WKT
          </button>
        </div>
      )}

      {/* Error message */}
      {error && (
        <div className="rounded-md px-2 py-1.5 text-[10px]" style={{ backgroundColor: C.red + "22", color: C.red, border: `1px solid ${C.red}44` }}>
          {error}
        </div>
      )}

      {/* Polygon list */}
      {polygons.length > 0 && (
        <div className="space-y-1">
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-semibold uppercase tracking-wider" style={{ color: C.text2 }}>
              Poligonos cargados ({polygons.length})
            </span>
            {polygons.length > 1 && (
              <button
                onClick={() => onPolygonsChange([])}
                className="text-[9px] px-1.5 py-0.5 rounded hover:bg-red-500/10 transition-colors"
                style={{ color: C.red }}
              >
                Borrar todos
              </button>
            )}
          </div>

          {polygons.map((poly) => (
            <div
              key={poly.id}
              className="rounded-md border p-2 transition-colors"
              style={{
                borderColor: poly.visible ? poly.color + "66" : C.border,
                backgroundColor: poly.visible ? poly.color + "0a" : "transparent",
              }}
            >
              {/* Header row */}
              <div className="flex items-center gap-2">
                {/* Color dot + visibility toggle */}
                <button
                  onClick={() => handleToggleVisibility(poly.id)}
                  className="flex-shrink-0 w-3 h-3 rounded-full border-2 transition-all"
                  style={{
                    borderColor: poly.color,
                    backgroundColor: poly.visible ? poly.color : "transparent",
                    opacity: poly.visible ? 1 : 0.4,
                  }}
                  title={poly.visible ? "Ocultar" : "Mostrar"}
                />

                {/* Name + info */}
                <div className="flex-1 min-w-0">
                  <div className="text-[11px] font-medium truncate" style={{ color: C.text1 }}>{poly.name}</div>
                  <div className="text-[9px]" style={{ color: C.text2 }}>
                    {poly.format} · {poly.polygon_count} poligono{poly.polygon_count > 1 ? "s" : ""}
                  </div>
                </div>

                {/* Actions */}
                <div className="flex gap-1 flex-shrink-0">
                  {/* Zoom to */}
                  {poly.bbox && (
                    <button
                      onClick={() => handleZoomTo(poly)}
                      className="p-1 rounded hover:bg-white/10 transition-colors"
                      title="Zoom al poligono"
                    >
                      <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke={C.text2} strokeWidth={2}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0zM10 7v3m0 0v3m0-3h3m-3 0H7" />
                      </svg>
                    </button>
                  )}

                  {/* Use as AOI */}
                  <button
                    onClick={() => handleUseAsAoi(poly)}
                    className="p-1 rounded hover:bg-white/10 transition-colors"
                    title="Usar como AOI"
                  >
                    <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke={C.green} strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M9 20l-5.447-2.724A1 1 0 013 16.382V5.618a1 1 0 011.447-.894L9 7m0 13l6-3m-6 3V7m6 10l4.553 2.276A1 1 0 0021 18.382V7.618a1 1 0 00-.553-.894L15 4m0 13V4m0 0L9 7" />
                    </svg>
                  </button>

                  {/* Delete */}
                  <button
                    onClick={() => handleRemove(poly.id)}
                    className="p-1 rounded hover:bg-red-500/20 transition-colors"
                    title="Eliminar"
                  >
                    <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke={C.red} strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                    </svg>
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
