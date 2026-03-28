Set-Location d:\MACOV\APEX
$content = @'
import { useEffect, useRef, useState } from "react"
import maplibregl from "maplibre-gl"
import "maplibre-gl/dist/maplibre-gl.css"
import { TerraDraw, TerraDrawMapLibreGLAdapter, TerraDrawPolygonMode } from "terra-draw"
import JobStatus from "./JobStatus"
import StatsCard from "./StatsCard"

const PREFIX = "apex-"

interface MapViewProps {
  onAnalyze: (aoi: object, engines: string[]) => void
  jobId: string | null
  results: any | null
}

export default function MapView({ onAnalyze, jobId, results }: MapViewProps) {
  const mapContainer = useRef<HTMLDivElement>(null)
  const map = useRef<maplibregl.Map | null>(null)
  const [aoi, setAoi] = useState<object | null>(null)
  const [engines, setEngines] = useState<string[]>(["deforestation", "vegetation", "structures"])

  useEffect(() => {
    if (map.current || !mapContainer.current) return
    map.current = new maplibregl.Map({
      container: mapContainer.current,
      style: "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
      center: [-89.65, 20.5],
      zoom: 9
    })
    map.current.on("load", () => {
      const draw = new TerraDraw({
        adapter: new TerraDrawMapLibreGLAdapter({ map: map.current! }),
        modes: [new TerraDrawPolygonMode()]
      })
      draw.start()
      draw.on("finish", (_id: string) => {
        const snapshot = draw.getSnapshot()
        if (snapshot.length > 0) {
          setAoi(snapshot[snapshot.length - 1].geometry)
        }
      })
    })
  }, [])

  useEffect(() => {
    if (!map.current || !results) return
    const layers = ["deforestation", "structures", "vegetation"]
    layers.forEach((layer) => {
      const id = PREFIX + layer
      if (map.current!.getLayer(id)) map.current!.removeLayer(id)
      if (map.current!.getSource(id)) map.current!.removeSource(id)
    })
    if (results.layers?.deforestation) {
      map.current.addSource(PREFIX + "deforestation", { type: "geojson", data: results.layers.deforestation })
      map.current.addLayer({ id: PREFIX + "deforestation", type: "fill", source: PREFIX + "deforestation", paint: { "fill-color": "#ef4444", "fill-opacity": 0.5 } })
    }
    if (results.layers?.structures) {
      map.current.addSource(PREFIX + "structures", { type: "geojson", data: results.layers.structures })
      map.current.addLayer({ id: PREFIX + "structures", type: "fill", source: PREFIX + "structures", paint: { "fill-color": "#22d3ee", "fill-opacity": 0.4 } })
    }
    if (results.layers?.vegetation) {
      map.current.addSource(PREFIX + "vegetation", { type: "geojson", data: results.layers.vegetation })
      map.current.addLayer({ id: PREFIX + "vegetation", type: "fill", source: PREFIX + "vegetation", paint: { "fill-color": "#22c55e", "fill-opacity": 0.4 } })
    }
  }, [results])

  const toggleEngine = (engine: string) => {
    setEngines((prev) => prev.includes(engine) ? prev.filter((e) => e !== engine) : [...prev, engine])
  }

  return (
    <div className="relative w-screen h-screen">
      <div ref={mapContainer} className="w-full h-full" />
      <div className="absolute top-4 left-4 bg-gray-900 text-white p-4 rounded-lg w-52 z-10">
        <h1 className="text-green-400 font-bold text-lg">APEX</h1>
        <p className="text-gray-400 text-xs mb-3">Analisis Predictivo de Ecosistemas con IA</p>
        <p className="text-sm font-medium mb-2">Motores a ejecutar</p>
        {["deforestation", "vegetation", "structures"].map((e) => (
          <label key={e} className="flex items-center gap-2 text-sm mb-1 cursor-pointer">
            <input type="checkbox" checked={engines.includes(e)} onChange={() => toggleEngine(e)} className="accent-green-400" />
            {e === "deforestation" ? "Deforestacion (U-Net)" : e === "vegetation" ? "Vegetacion (RF)" : "Estructuras (Mask R-CNN)"}
          </label>
        ))}
        <button
          onClick={() => aoi && onAnalyze(aoi, engines)}
          disabled={!aoi}
          className="mt-3 w-full bg-green-500 hover:bg-green-600 disabled:bg-gray-600 disabled:cursor-not-allowed text-white text-sm font-bold py-2 rounded transition-colors"
        >
          {aoi ? "Analizar Area Seleccionada" : "Dibuja un poligono"}
        </button>
        {jobId && <JobStatus jobId={jobId} />}
        {results && <StatsCard results={results} />}
      </div>
    </div>
  )
}
'@
[System.IO.File]::WriteAllText("frontend\src\components\MapView.tsx", $content, [System.Text.Encoding]::UTF8)
Write-Host "MapView.tsx reescrito en UTF-8"
