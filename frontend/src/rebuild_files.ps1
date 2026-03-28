$utf8 = [System.Text.Encoding]::UTF8

$mainContent = @'
import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.tsx'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
'@
Remove-Item -Force d:\MACOV\APEX\frontend\src\main.tsx -ErrorAction SilentlyContinue
[System.IO.File]::WriteAllText("d:\MACOV\APEX\frontend\src\main.tsx", $mainContent, $utf8)

$appContent = @'
import React from 'react'
import MapView from './components/MapView'

function App() {
  return (
    <MapView />
  )
}

export default App
'@
Remove-Item -Force d:\MACOV\APEX\frontend\src\App.tsx -ErrorAction SilentlyContinue
[System.IO.File]::WriteAllText("d:\MACOV\APEX\frontend\src\App.tsx", $appContent, $utf8)

$mapViewContent = @'
import React, { useEffect, useRef, useState } from 'react';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import MapboxDraw from '@mapbox/mapbox-gl-draw';
import '@mapbox/mapbox-gl-draw/dist/mapbox-gl-draw.css';
import JobStatus from './JobStatus';
import StatsCard from './StatsCard';

export default function MapView() {
  const mapContainer = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const draw = useRef<MapboxDraw | null>(null);

  const [engines, setEngines] = useState({
    deforestation: true,
    vegetation: true,
    structures: true
  });
  
  const [jobId, setJobId] = useState<string | null>(null);
  const [results, setResults] = useState<any | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  useEffect(() => {
    if (map.current || !mapContainer.current) return;

    map.current = new maplibregl.Map({
      container: mapContainer.current,
      style: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
      center: [-89.65, 20.5], // Yucatán test area
      zoom: 10
    });

    draw.current = new MapboxDraw({
      displayControlsDefault: false,
      controls: {
        polygon: true,
        trash: true
      },
      defaultMode: 'draw_polygon'
    });

    // @ts-ignore
    map.current.addControl(draw.current);

    map.current.on('draw.create', () => {
        // limit to 1 shape
        if (draw.current) {
            const data = draw.current.getAll();
            if (data.features.length > 1) {
                draw.current.delete(data.features[0].id as string);
            }
        }
    });

  }, []);

  const handleAnalyze = async () => {
    if (!draw.current) return;
    const data = draw.current.getAll();
    if (data.features.length === 0) {
      setErrorMsg("Dibuja un polígono primero.");
      return;
    }
    setErrorMsg(null);
    setResults(null);
    setJobId(null);
    
    ['deforestation', 'structures', 'vegetation'].forEach(layer => {
      if (map.current?.getLayer(`apex-${layer}`)) map.current.removeLayer(`apex-${layer}`);
      if (map.current?.getSource(`apex-${layer}`)) map.current.removeSource(`apex-${layer}`);
    });

    const activeEngines = Object.entries(engines)
      .filter(([_, active]) => active)
      .map(([name]) => name);

    try {
      const res = await fetch("http://localhost:8002/api/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          aoi: data.features[0].geometry,
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
    <div className="relative w-full h-screen">
      <div ref={mapContainer} className="absolute inset-0" />
      <div className="absolute top-4 left-4 z-10 bg-gray-900 bg-opacity-90 p-4 rounded-lg shadow-xl border border-gray-700 w-[340px] text-white text-left max-h-[90vh] overflow-y-auto">
        <h1 className="text-xl font-bold text-green-400 mb-2">🌿 APEX</h1>
        <p className="text-xs text-gray-300 mb-4">Análisis Predictivo de Ecosistemas con IA</p>
        
        {!results && (
          <div className="space-y-4">
            <div>
              <h2 className="text-sm font-semibold mb-2">Motores a ejecutar</h2>
              <div className="flex flex-col gap-2">
                <label className="flex items-center space-x-2 text-sm">
                  <input type="checkbox" checked={engines.deforestation} onChange={(e) => setEngines({...engines, deforestation: e.target.checked})} className="accent-green-500" />
                  <span>Deforestación (U-Net)</span>
                </label>
                <label className="flex items-center space-x-2 text-sm">
                  <input type="checkbox" checked={engines.vegetation} onChange={(e) => setEngines({...engines, vegetation: e.target.checked})} className="accent-green-500" />
                  <span>Vegetación (RF)</span>
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
              {jobId && !results ? 'Procesando...' : 'Analizar Área Seleccionada'}
            </button>
            {errorMsg && <p className="text-red-400 text-xs mt-2">{errorMsg}</p>}
          </div>
        )}

        {jobId && !results && <JobStatus jobId={jobId} onCompleted={handleResultsCompleted} />}

        {results && (
            <div className="mt-4 border-t border-gray-700 pt-4">
                <h2 className="text-sm font-bold mb-3">Resultados del Análisis</h2>
                {results.deforestation && <StatsCard engine="deforestation" stats={results.deforestation.stats} />}
                {results.structures && <StatsCard engine="structures" stats={results.structures.stats} />}
                {results.vegetation && <StatsCard engine="vegetation" stats={results.vegetation.stats} />}
                
                <button 
                  onClick={() => { setResults(null); setJobId(null); }}
                  className="mt-4 w-full bg-gray-700 hover:bg-gray-600 text-white text-sm py-2 rounded">
                  Nuevo Análisis
                </button>
            </div>
        )}
      </div>
    </div>
  );
}
'@
Remove-Item -Force d:\MACOV\APEX\frontend\src\components\MapView.tsx -ErrorAction SilentlyContinue
[System.IO.File]::WriteAllText("d:\MACOV\APEX\frontend\src\components\MapView.tsx", $mapViewContent, $utf8)

$jobStatusContent = @'
import React, { useEffect, useState } from 'react';

interface JobStatusProps {
  jobId: string;
  onCompleted: (results: any) => void;
}

export default function JobStatus({ jobId, onCompleted }: JobStatusProps) {
  const [progress, setProgress] = useState(0);
  const [step, setStep] = useState('Iniciando...');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!jobId) return;

    const interval = setInterval(async () => {
      try {
        const res = await fetch(`http://localhost:8002/api/jobs/${jobId}`);
        const data = await res.json();
        
        if (data.status === 'failed') {
            setError(data.current_step);
            clearInterval(interval);
        } else {
            setProgress(data.progress || 0);
            setStep(data.current_step || 'Procesando...');

            if (data.status === 'completed') {
            clearInterval(interval);
            const resultRes = await fetch(`http://localhost:8002/api/results/${jobId}`);
            const resultData = await resultRes.json();
            onCompleted(resultData);
            }
        }
      } catch (e: any) {
        console.error('Error polling job:', e);
      }
    }, 2000);

    return () => clearInterval(interval);
  }, [jobId, onCompleted]);

  if (error) {
    return (
        <div className="mt-4 bg-red-900 bg-opacity-50 p-3 rounded text-red-100 text-sm">
            <p className="font-bold">Error durante el análisis:</p>
            <p>{error}</p>
        </div>
    );
  }

  return (
    <div className="mt-4">
      <div className="flex justify-between text-xs text-gray-300 mb-1">
        <span>{step}</span>
        <span>{progress}%</span>
      </div>
      <div className="h-2 w-full bg-gray-700 rounded overflow-hidden">
        <div 
          className="h-full bg-green-500 transition-all duration-500 ease-out"
          style={{ width: `${progress}%` }}
        />
      </div>
    </div>
  );
}
'@
Remove-Item -Force d:\MACOV\APEX\frontend\src\components\JobStatus.tsx -ErrorAction SilentlyContinue
[System.IO.File]::WriteAllText("d:\MACOV\APEX\frontend\src\components\JobStatus.tsx", $jobStatusContent, $utf8)

$statsCardContent = @'
import React from 'react';
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from 'recharts';

export default function StatsCard({ engine, stats }: { engine: string, stats: any }) {
    if (engine === 'deforestation') {
        return (
            <div className="bg-gray-800 p-3 rounded-lg border border-red-900/50 mb-3">
                <h3 className="text-red-400 font-bold mb-2">🔴 Deforestación Detectada</h3>
                <div className="flex justify-between">
                    <span className="text-gray-300">Área afectada:</span>
                    <span className="text-white font-bold">{stats.area_ha.toFixed(1)} ha</span>
                </div>
                <div className="flex justify-between mt-1">
                    <span className="text-gray-300">Pérdida (%):</span>
                    <span className="text-red-400 font-bold">-{stats.percent_lost.toFixed(1)}%</span>
                </div>
            </div>
        );
    }

    if (engine === 'structures') {
        return (
            <div className="bg-gray-800 p-3 rounded-lg border border-cyan-900/50 mb-3">
                <h3 className="text-cyan-400 font-bold mb-2">🏗️ Estructuras Identificadas</h3>
                <div className="flex justify-between">
                    <span className="text-gray-300">Total detectadas:</span>
                    <span className="text-white font-bold">{stats.count}</span>
                </div>
                <div className="mt-2 text-xs text-gray-400">
                    <p>Edificios: {stats.types?.building || 0}</p>
                    <p>Infra. Agrícola/Solar: {stats.types?.solar_panel || 0}</p>
                </div>
            </div>
        );
    }

    if (engine === 'vegetation') {
        const data = [
            { name: 'Bosque denso', value: stats.classes.bosque_denso, color: '#166534' },
            { name: 'Bosque ralo', value: stats.classes.bosque_ralo, color: '#22c55e' },
            { name: 'Pastizal', value: stats.classes.pastizal, color: '#84cc16' },
            { name: 'Suelo', value: stats.classes.suelo, color: '#854d0e' },
            { name: 'Agua', value: stats.classes.agua, color: '#0369a1' },
        ];

        return (
            <div className="bg-gray-800 p-3 rounded-lg border border-green-900/50 mb-3">
                <h3 className="text-green-400 font-bold mb-2">🌿 Clases de Vegetación</h3>
                <div className="h-40 w-full">
                    <ResponsiveContainer width="100%" height="100%">
                        <PieChart>
                            <Pie
                                data={data}
                                cx="50%"
                                cy="50%"
                                innerRadius={30}
                                outerRadius={45}
                                paddingAngle={2}
                                dataKey="value"
                            >
                                {data.map((entry: any, index: number) => (
                                    <Cell key={`cell-${index}`} fill={entry.color} />
                                ))}
                            </Pie>
                            <Tooltip contentStyle={{ backgroundColor: '#1f2937', border: 'none' }} itemStyle={{ color: '#fff' }} />
                        </PieChart>
                    </ResponsiveContainer>
                </div>
            </div>
        );
    }

    return null;
}
'@
Remove-Item -Force d:\MACOV\APEX\frontend\src\components\StatsCard.tsx -ErrorAction SilentlyContinue
[System.IO.File]::WriteAllText("d:\MACOV\APEX\frontend\src\components\StatsCard.tsx", $statsCardContent, $utf8)

$indexCssContent = @'
@import "tailwindcss";

html, body, #root {
  margin: 0;
  padding: 0;
  height: 100%;
  width: 100%;
}
'@
Remove-Item -Force d:\MACOV\APEX\frontend\src\index.css -ErrorAction SilentlyContinue
[System.IO.File]::WriteAllText("d:\MACOV\APEX\frontend\src\index.css", $indexCssContent, $utf8)

Write-Host "Archivos .tsx reconstruidos exitosamente en UTF-8 usando PowerShell."
