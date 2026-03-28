$content = @"
import React, { useEffect, useRef, useState } from 'react';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { TerraDraw, TerraDrawPolygonMode } from 'terra-draw';
import { TerraDrawMapLibreGLAdapter } from 'terra-draw-maplibre-gl-adapter';
import JobStatus from './JobStatus';
import StatsCard from './StatsCard';

export default function MapView() {
  const mapContainer = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const draw = useRef<TerraDraw | null>(null);

  const [engines, setEngines] = useState({
    deforestation: true,
    vegetation: true,
    structures: true
  });
  
  const [jobId, setJobId] = useState<string | null>(null);
  const [results, setResults] = useState<any | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [aoi, setAoi] = useState<any | null>(null);

  useEffect(() => {
    if (map.current || !mapContainer.current) return;

    map.current = new maplibregl.Map({
      container: mapContainer.current,
      style: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
      center: [-89.65, 20.5], // Yucatan test area
      zoom: 10
    });

    map.current.on('load', () => {
        if (!map.current) return;
        draw.current = new TerraDraw({
          adapter: new TerraDrawMapLibreGLAdapter({ map: map.current }),
          modes: [new TerraDrawPolygonMode()]
        });
        
        draw.current.start();
        draw.current.setMode('polygon');

        draw.current.on('finish', (id) => {
          if (!draw.current) return;
          const snapshot = draw.current.getSnapshot();
          const feature = snapshot.find(f => f.id === id);
          if (feature) {
            setAoi(feature.geometry);
            draw.current.clear();
            draw.current.addFeatures([{
                ...feature
            }]);
          }
        });
    });

  }, []);

  const handleAnalyze = async () => {
    if (!aoi) {
      setErrorMsg("Dibuja un polígono primero.");
      return;
    }
    setErrorMsg(null);
    setResults(null);
    setJobId(null);
    
    ['deforestation', 'structures', 'vegetation'].forEach(layer => {
      if (map.current?.getLayer(`apex-` + layer)) map.current.removeLayer(`apex-` + layer);
      if (map.current?.getSource(`apex-` + layer)) map.current.removeSource(`apex-` + layer);
    });

    const activeEngines = Object.entries(engines)
      .filter(([_, active]) => active)
      .map(([name]) => name);

    try {
      const res = await fetch("http://localhost:8002/api/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          aoi: aoi,
          engines: activeEngines,
          date_range: ["2022-01-01", "2023-12-31"]
        })
      });
      const resData = await res.json();
      setJobId(resData.job_id);
    } catch (e: any) {
      setErrorMsg("Error conectando con el backend: " + e.message);
    }
  };

  const handleResultsCompleted = (data: any) => {
    setResults(data.layers);
    
    if (!map.current) return;

    if (data.layers.deforestation) {
      map.current.addSource('apex-deforestation', {
        type: 'geojson',
        data: data.layers.deforestation.geojson
      });
      map.current.addLayer({
        id: 'apex-deforestation',
        type: 'fill',
        source: 'apex-deforestation',
        paint: {
          'fill-color': '#f87171',
          'fill-opacity': 0.5
        }
      });
    }

    if (data.layers.structures) {
      map.current.addSource('apex-structures', {
        type: 'geojson',
        data: data.layers.structures.geojson
      });
      map.current.addLayer({
        id: 'apex-structures',
        type: 'fill',
        source: 'apex-structures',
        paint: {
          'fill-color': '#22d3ee',
          'fill-opacity': 0.3,
          'fill-outline-color': '#06b6d4'
        }
      });
    }
  };

  return (
    <div className="w-screen h-screen relative bg-gray-800">
      <div ref={mapContainer} className="absolute inset-0 w-full h-full" style={{ width: '100vw', height: '100vh' }} />
      <div className="absolute top-4 left-4 z-10 bg-gray-900 bg-opacity-90 p-4 rounded-lg shadow-xl border border-gray-700 w-[340px] text-white text-left max-h-[90vh] overflow-y-auto">
        <h1 className="text-xl font-bold text-green-400 mb-2">[APEX]</h1>
        <p className="text-xs text-gray-300 mb-4">Analisis Predictivo de Ecosistemas con IA</p>
        
        {!results && (
          <div className="space-y-4">
            <div>
              <h2 className="text-sm font-semibold mb-2">Motores a ejecutar</h2>
              <div className="flex flex-col gap-2">
                <label className="flex items-center space-x-2 text-sm">
                  <input type="checkbox" checked={engines.deforestation} onChange={(e) => setEngines({...engines, deforestation: e.target.checked})} className="accent-green-500" />
                  <span>Deforestacion (U-Net)</span>
                </label>
                <label className="flex items-center space-x-2 text-sm">
                  <input type="checkbox" checked={engines.vegetation} onChange={(e) => setEngines({...engines, vegetation: e.target.checked})} className="accent-green-500" />
                  <span>Vegetacion (RF)</span>
                </label>
                <label className="flex items-center space-x-2 text-sm">
                  <input type="checkbox" checked={engines.structures} onChange={(e) => setEngines({...engines, structures: e.target.checked})} className="accent-green-500" />
                  <span>Estructuras (Mask R-CNN)</span>
                </label>
              </div>
            </div>
            
            <button 
              onClick={handleAnalyze}
              disabled={!!jobId && !results}
              className="w-full bg-green-600 hover:bg-green-500 disabled:bg-gray-700 disabled:text-gray-500 text-white font-bold py-2 px-4 rounded transition-colors mt-4">
              {jobId && !results ? 'Procesando...' : 'Analizar Area Seleccionada'}
            </button>
            {errorMsg && <p className="text-red-400 text-xs mt-2">{errorMsg}</p>}
          </div>
        )}

        {jobId && !results && <JobStatus jobId={jobId} onCompleted={handleResultsCompleted} />}

        {results && (
            <div className="mt-4 border-t border-gray-700 pt-4">
                <h2 className="text-sm font-bold mb-3">Resultados del Analisis</h2>
                {results.deforestation && <StatsCard engine="deforestation" stats={results.deforestation.stats} />}
                {results.structures && <StatsCard engine="structures" stats={results.structures.stats} />}
                {results.vegetation && <StatsCard engine="vegetation" stats={results.vegetation.stats} />}
                
                <button 
                  onClick={() => { setResults(null); setJobId(null); setAoi(null); if (draw.current) { draw.current.clear(); draw.current.setMode('polygon'); } }}
                  className="mt-4 w-full bg-gray-700 hover:bg-gray-600 text-white text-sm py-2 rounded">
                  Nuevo Analisis
                </button>
            </div>
        )}
      </div>
    </div>
  );
}
"@

[System.IO.File]::WriteAllText("D:\MACOV\APEX\frontend\src\components\MapView.tsx", $content, [System.Text.Encoding]::UTF8)
Write-Host "Archivo MapView.tsx reescrito usando PowerShell UTF8 sin BOM."
