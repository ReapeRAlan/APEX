import { useState } from "react"
import { PieChart, Pie, Cell, Tooltip } from "recharts"

const C = {
  bgCard: "#21262d",
  border: "#30363d",
  text1: "#e6edf3",
  text2: "#8b949e",
} as const

const VEG_COLORS: Record<string, string> = {
  bosque_denso: "#166534", bosque_ralo: "#22c55e", pastizal: "#86efac",
  suelo: "#92400e", agua: "#3b82f6", urbano: "#6b21a8", quemado: "#7c2d12",
  manglar_inundado: "#7a87c6", cultivos: "#e49635", matorral: "#dfc35a", nieve: "#b39fe1",
}
const VEG_LABELS: Record<string, string> = {
  bosque_denso: "Bosque denso", bosque_ralo: "Bosque ralo", pastizal: "Pastizal",
  suelo: "Suelo", agua: "Agua", urbano: "Urbano", quemado: "Quemado",
  manglar_inundado: "Manglar", cultivos: "Cultivos", matorral: "Matorral", nieve: "Nieve",
}

function ConfidenceBadge({ value }: { value: number }) {
  const pct = Math.round(value * 100)
  const color = pct >= 70 ? "#2ea043" : pct >= 40 ? "#d29922" : "#f85149"
  return (
    <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-bold"
      style={{ backgroundColor: color + "22", color, border: `1px solid ${color}44` }}>
      <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: color }} />
      {pct}%
    </span>
  )
}

function Stat({ label, value, accent }: { label: string; value: string | number; accent?: string }) {
  return (
    <div className="flex justify-between items-center py-1">
      <span className="text-[11px]" style={{ color: C.text2 }}>{label}</span>
      <span className="text-[11px] font-semibold" style={{ color: accent ?? C.text1 }}>{value}</span>
    </div>
  )
}

function EngineCard({ title, color, mainValue, mainLabel, children, defaultOpen = false }: {
  title: string; color: string; mainValue: string; mainLabel: string; children: React.ReactNode; defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="rounded-lg overflow-hidden" style={{ backgroundColor: C.bgCard, border: `1px solid ${C.border}` }}>
      {/* Header */}
      <button onClick={() => setOpen(!open)} className="w-full px-3 py-2.5 flex items-center gap-3 hover:bg-white/5 transition-colors">
        <span className="w-1 h-8 rounded-full flex-shrink-0" style={{ backgroundColor: color }} />
        <div className="flex-1 text-left min-w-0">
          <p className="text-[11px] font-medium" style={{ color }}>{title}</p>
          <p className="text-lg font-bold leading-tight" style={{ color: C.text1 }}>{mainValue}</p>
          <p className="text-[10px]" style={{ color: C.text2 }}>{mainLabel}</p>
        </div>
        <svg className={`w-4 h-4 flex-shrink-0 transition-transform ${open ? "rotate-180" : ""}`}
          fill="none" viewBox="0 0 24 24" stroke={C.text2} strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {/* Details */}
      {open && (
        <div className="px-3 pb-3 border-t" style={{ borderColor: C.border }}>
          {children}
        </div>
      )}
    </div>
  )
}

export default function StatsCard({ results }: { results: any }) {
  return (
    <div className="space-y-2">
      {/* ── Deforestation ── */}
      {results.layers?.deforestation?.stats && (() => {
        const s = results.layers.deforestation.stats
        return (
          <EngineCard title="Deforestacion Detectada" color="#f85149"
            mainValue={`${s.area_ha?.toFixed?.(1) ?? s.area_ha ?? "?"} ha`}
            mainLabel={`${s.n_features ?? "?"} zonas afectadas`} defaultOpen>
            <Stat label="Perdida del AOI" value={`-${s.percent_lost?.toFixed?.(1) ?? s.percent_lost ?? "?"}%`} accent="#f85149" />
            <div className="flex justify-between items-center py-1">
              <span className="text-[11px]" style={{ color: C.text2 }}>Confianza</span>
              <ConfidenceBadge value={s.confidence ?? 0} />
            </div>
            <Stat label="Fuente" value={s.source === "Dynamic World T1->T2" ? "Dynamic World" : (s.source ?? "DW")} />
          </EngineCard>
        )
      })()}

      {/* ── Urban Expansion ── */}
      {results.layers?.urban_expansion?.stats && (() => {
        const s = results.layers.urban_expansion.stats
        return (
          <EngineCard title="Expansion Urbana" color="#f0883e"
            mainValue={`${s.area_ha ?? "?"} ha`}
            mainLabel={`${s.n_features ?? "?"} zonas detectadas`}>
            <Stat label="% del AOI" value={`${s.percent_changed ?? "?"}%`} accent="#f0883e" />
            <Stat label="Fuente" value="Dynamic World T1→T2" />
          </EngineCard>
        )
      })()}

      {/* ── Structures ── */}
      {results.layers?.structures?.stats && (() => {
        const s = results.layers.structures.stats
        const types = s.types ?? {}
        return (
          <EngineCard title="Estructuras Identificadas" color="#58a6ff"
            mainValue={`${s.count ?? 0}`}
            mainLabel="estructuras detectadas">
            {Object.entries(types).map(([k, v]) => (
              <Stat key={k} label={k.replace("_", " ")} value={v as number} accent="#58a6ff" />
            ))}
          </EngineCard>
        )
      })()}

      {/* ── Vegetation ── */}
      {results.layers?.vegetation?.stats && (() => {
        const s = results.layers.vegetation.stats
        const classes = s.classes ?? {}
        const data = Object.entries(classes)
          .filter(([, v]) => (v as number) > 0)
          .map(([key, value]) => ({
            name: VEG_LABELS[key] ?? key,
            value: value as number,
            color: VEG_COLORS[key] ?? "#6b7280",
          }))
          .sort((a, b) => b.value - a.value)
        return (
          <EngineCard title="Clases de Vegetacion" color="#2ea043"
            mainValue={`${data.length} clases`}
            mainLabel="clasificadas por Dynamic World">
            <div className="flex justify-center py-2">
              <PieChart width={170} height={170}>
                <Pie data={data} cx={85} cy={85} innerRadius={38} outerRadius={68} paddingAngle={2} dataKey="value">
                  {data.map((entry, i) => <Cell key={`c-${i}`} fill={entry.color} />)}
                </Pie>
                <Tooltip
                  contentStyle={{ backgroundColor: "#161b22", border: `1px solid ${C.border}`, borderRadius: "8px", fontSize: "11px" }}
                  itemStyle={{ color: "#fff" }}
                  formatter={(value, name) => [`${Number(value).toFixed(1)}%`, name]}
                />
              </PieChart>
            </div>
            <div className="grid grid-cols-2 gap-x-2 gap-y-1">
              {data.map((d) => (
                <div key={d.name} className="flex items-center gap-1.5">
                  <span className="w-2 h-2 rounded-sm flex-shrink-0" style={{ backgroundColor: d.color }} />
                  <span className="text-[10px] truncate" style={{ color: C.text2 }}>{d.name}</span>
                  <span className="text-[10px] font-medium ml-auto" style={{ color: C.text1 }}>{d.value}%</span>
                </div>
              ))}
            </div>
          </EngineCard>
        )
      })()}

      {/* ── Hansen Forest Loss ── */}
      {results.layers?.hansen?.stats && (() => {
        const s = results.layers.hansen.stats
        const lossByYear = s.loss_by_year ?? {}
        return (
          <EngineCard title="Hansen Forest Loss" color="#facc15"
            mainValue={`${s.total_loss_ha?.toFixed?.(1) ?? s.total_loss_ha ?? "?"} ha`}
            mainLabel={`${s.n_features ?? "?"} zonas (2001-2024)`}>
            <Stat label="Cobertura media original" value={`${s.avg_treecover_pct?.toFixed?.(0) ?? "?"}%`} accent="#facc15" />
            <div className="flex justify-between items-center py-1">
              <span className="text-[11px]" style={{ color: C.text2 }}>Confianza</span>
              <ConfidenceBadge value={s.confidence ?? 0} />
            </div>
            {Object.keys(lossByYear).length > 0 && (
              <div className="mt-2 space-y-1">
                <p className="text-[10px] font-bold" style={{ color: C.text2 }}>Perdida por ano</p>
                {Object.entries(lossByYear).sort(([a], [b]) => Number(a) - Number(b)).map(([yr, ha]) => (
                  <div key={yr} className="flex justify-between">
                    <span className="text-[10px]" style={{ color: C.text2 }}>{yr}</span>
                    <span className="text-[10px] font-medium" style={{ color: "#facc15" }}>{(ha as number).toFixed?.(1)} ha</span>
                  </div>
                ))}
              </div>
            )}
            <Stat label="Fuente" value="Hansen GFC v1.12 (UMD)" />
          </EngineCard>
        )
      })()}

      {/* ── Alerts GLAD/RADD ── */}
      {results.layers?.alerts?.stats && (() => {
        const s = results.layers.alerts.stats
        return (
          <EngineCard title="Alertas GLAD / RADD" color="#dc2626"
            mainValue={`${s.total_alerts ?? s.n_features ?? "?"}`}
            mainLabel="alertas de deforestacion activas">
            <Stat label="Alertas GLAD-S2" value={s.glad_count ?? "?"} accent="#dc2626" />
            <Stat label="Alertas RADD (SAR)" value={s.radd_count ?? "?"} accent="#fb923c" />
            <Stat label="Area total" value={`${s.total_area_ha?.toFixed?.(1) ?? "?"} ha`} />
            <Stat label="Confirmadas" value={s.confirmed_count ?? "?"} accent="#2ea043" />
            <Stat label="Fuente" value="GLAD-S2 + RADD (WUR)" />
          </EngineCard>
        )
      })()}

      {/* ── Drivers of Forest Loss ── */}
      {results.layers?.drivers?.stats && (() => {
        const s = results.layers.drivers.stats
        const DRIVER_COLORS: Record<string, string> = {
          "Agricultura permanente": "#22c55e", "Commodities (mineria/energia)": "#f59e0b",
          "Cultivo rotacional": "#84cc16", "Tala": "#dc2626", "Incendios": "#ef4444",
          "Asentamientos e infraestructura": "#8b5cf6", "Perturbacion natural": "#6b7280",
        }
        const drivers = s.drivers ?? {}
        const driverData = Object.entries(drivers)
          .filter(([, v]) => (v as number) > 0)
          .map(([key, value]) => ({
            name: key,
            value: value as number,
            color: DRIVER_COLORS[key] ?? "#a78bfa",
          }))
          .sort((a, b) => b.value - a.value)
        return (
          <EngineCard title="Drivers de Deforestacion" color="#8b5cf6"
            mainValue={`${driverData.length} causas`}
            mainLabel="clasificadas por WRI/Google DeepMind">
            {driverData.length > 0 && (
              <div className="flex justify-center py-2">
                <PieChart width={170} height={170}>
                  <Pie data={driverData} cx={85} cy={85} innerRadius={38} outerRadius={68} paddingAngle={2} dataKey="value">
                    {driverData.map((entry, i) => <Cell key={`d-${i}`} fill={entry.color} />)}
                  </Pie>
                  <Tooltip
                    contentStyle={{ backgroundColor: "#161b22", border: `1px solid ${C.border}`, borderRadius: "8px", fontSize: "11px" }}
                    itemStyle={{ color: "#fff" }}
                    formatter={(value, name) => [`${Number(value).toFixed(1)}%`, name]}
                  />
                </PieChart>
              </div>
            )}
            <div className="space-y-1">
              {driverData.map((d) => (
                <div key={d.name} className="flex items-center gap-1.5">
                  <span className="w-2 h-2 rounded-sm flex-shrink-0" style={{ backgroundColor: d.color }} />
                  <span className="text-[10px] truncate flex-1" style={{ color: C.text2 }}>{d.name}</span>
                  <span className="text-[10px] font-medium" style={{ color: C.text1 }}>{d.value.toFixed?.(1)}%</span>
                </div>
              ))}
            </div>
            <Stat label="Fuente" value="WRI / Google DeepMind" />
          </EngineCard>
        )
      })()}

      {/* ── Fire / Burned Area ── */}
      {results.layers?.fire?.stats && (() => {
        const s = results.layers.fire.stats
        return (
          <EngineCard title="Incendios / Areas Quemadas" color="#f97316"
            mainValue={`${s.total_burned_ha?.toFixed?.(1) ?? "?"} ha`}
            mainLabel={`${s.fire_count ?? "?"} areas quemadas detectadas`}>
            <Stat label="Correlacion con deforestacion" value={`${s.fire_related_deforestation_pct?.toFixed?.(0) ?? "?"}%`} accent="#f97316" />
            <Stat label="Fuente" value="MODIS MCD64A1 (area quemada mensual)" />
          </EngineCard>
        )
      })()}

      {/* ── FIRMS NRT Hotspots ── */}
      {results.layers?.firms_hotspots?.stats && (() => {
        const s = results.layers.firms_hotspots.stats
        return (
          <EngineCard title="FIRMS Hotspots (Tiempo Real)" color="#ff3b30"
            mainValue={`${s.hotspot_count ?? 0} hotspots`}
            mainLabel={`FRP total: ${s.total_frp_mw?.toFixed?.(1) ?? "?"} MW`}>
            <Stat label="Alta confianza" value={`${s.high_confidence_count ?? 0}`} accent="#ff3b30" />
            <Stat label="FRP promedio" value={`${s.avg_frp_mw?.toFixed?.(1) ?? "?"} MW`} />
            <Stat label="FRP maximo" value={`${s.max_frp_mw?.toFixed?.(1) ?? "?"} MW`} accent="#ff3b30" />
            <Stat label="Satelites" value={(s.satellites || []).join(", ") || "—"} />
            {s.cluster_count != null && <Stat label="Agrupaciones" value={`${s.cluster_count}`} />}
            {s.modis_crossmatch_pct != null && (
              <Stat label="Coincidencia MODIS" value={`${s.modis_crossmatch_pct.toFixed(1)}%`} accent="#f97316" />
            )}
            <Stat label="Periodo" value={s.date_range || "—"} />
            <Stat label="Fuente" value="NASA FIRMS (VIIRS/MODIS NRT)" />
          </EngineCard>
        )
      })()}

      {/* ── SAR Change Detection ── */}
      {results.layers?.sar?.stats && (() => {
        const s = results.layers.sar.stats
        return (
          <EngineCard title="Cambios SAR (Sentinel-1)" color="#06b6d4"
            mainValue={`${s.total_change_ha?.toFixed?.(1) ?? "?"} ha`}
            mainLabel={`${s.sar_change_count ?? 0} zonas de cambio`}>
            <Stat label="Alta confianza" value={`${s.high_confidence_count ?? 0}`} accent="#06b6d4" />
            <Stat label="Fuente" value="Sentinel-1 SAR (log-ratio)" />
          </EngineCard>
        )
      })()}

      {/* ── AVOCADO Anomalies ── */}
      {results.layers?.avocado?.stats && (() => {
        const s = results.layers.avocado.stats
        const SEV_COLORS: Record<string, string> = {
          critica: "#dc2626", alta: "#f97316", media: "#eab308", baja: "#22c55e",
        }
        const sevEntries = Object.entries(s.by_severity ?? {}).sort(
          ([a], [b]) => ["critica", "alta", "media", "baja"].indexOf(a) - ["critica", "alta", "media", "baja"].indexOf(b)
        )
        return (
          <EngineCard title="AVOCADO (Anomalias NDVI)" color="#a855f7"
            mainValue={`${s.n_anomalies ?? 0} anomalias`}
            mainLabel={`${s.total_area_ha ?? 0} ha afectadas`}>
            {sevEntries.map(([sev, count]) => (
              <Stat key={sev} label={sev.charAt(0).toUpperCase() + sev.slice(1)} value={count as number} accent={SEV_COLORS[sev] ?? "#a855f7"} />
            ))}
            <Stat label="Fuente" value="AVOCADO (S2 NDVI percentil)" />
          </EngineCard>
        )
      })()}

      {/* ── SpectralGPT ── */}
      {results.layers?.spectralgpt?.stats && (() => {
        const s = results.layers.spectralgpt.stats
        const classes = s.classes ?? {}
        const SGPT_COLORS: Record<string, string> = {
          vegetation_stress: "#ef4444", urban_change: "#8b5cf6", water_anomaly: "#3b82f6",
          soil_exposure: "#92400e", healthy_vegetation: "#22c55e",
        }
        const data = Object.entries(classes)
          .filter(([, v]) => (v as number) > 0)
          .map(([key, value]) => ({
            name: key.replace(/_/g, " "),
            value: value as number,
            color: SGPT_COLORS[key] ?? "#14b8a6",
          }))
          .sort((a, b) => b.value - a.value)
        return (
          <EngineCard title="SpectralGPT (LULC)" color="#14b8a6"
            mainValue={`${s.n_features ?? 0} detecciones`}
            mainLabel={`${data.length} clases espectrales`}>
            {data.map((d) => (
              <div key={d.name} className="flex items-center gap-1.5 py-0.5">
                <span className="w-2 h-2 rounded-sm flex-shrink-0" style={{ backgroundColor: d.color }} />
                <span className="text-[10px] truncate flex-1" style={{ color: C.text2 }}>{d.name}</span>
                <span className="text-[10px] font-medium" style={{ color: C.text1 }}>{d.value}%</span>
              </div>
            ))}
            <Stat label="Fuente" value="SpectralGPT (ESA)" />
          </EngineCard>
        )
      })()}

      {/* ── ForestNet-MX Drivers ── */}
      {results.layers?.drivers_mx?.stats && (() => {
        const s = results.layers.drivers_mx.stats
        const MX_COLORS: Record<string, string> = {
          ganaderia: "#22c55e", agricultura: "#84cc16", expansion_urbana: "#8b5cf6",
          incendio: "#ef4444", tala_ilegal: "#dc2626", infraestructura: "#f59e0b",
          plantacion: "#06b6d4", perturbacion_natural: "#6b7280",
        }
        const driverPct = s.driver_pct ?? {}
        const data = Object.entries(driverPct)
          .filter(([, v]) => (v as number) > 0)
          .map(([key, value]) => ({
            name: key.replace(/_/g, " "),
            value: value as number,
            color: MX_COLORS[key] ?? "#c084fc",
          }))
          .sort((a, b) => b.value - a.value)
        return (
          <EngineCard title="ForestNet-MX (Drivers)" color="#c084fc"
            mainValue={`${s.n_classified ?? 0} clasificados`}
            mainLabel={`Dominante: ${s.dominant_label ?? "—"}`}>
            {data.map((d) => (
              <div key={d.name} className="flex items-center gap-1.5 py-0.5">
                <span className="w-2 h-2 rounded-sm flex-shrink-0" style={{ backgroundColor: d.color }} />
                <span className="text-[10px] truncate flex-1" style={{ color: C.text2 }}>{d.name}</span>
                <span className="text-[10px] font-medium" style={{ color: C.text1 }}>{d.value}%</span>
              </div>
            ))}
            <Stat label="Fuente" value="ForestNet-MX (heuristic v1)" />
          </EngineCard>
        )
      })()}

      {/* ── Legal Context (ANP) ── */}
      {results.layers?.legal_context?.stats && (() => {
        const s = results.layers.legal_context.stats
        const isRisk = s.intersects_anp
        return (
          <EngineCard title="Contexto Legal" color={isRisk ? "#f85149" : "#2ea043"}
            mainValue={isRisk ? "DENTRO de ANP" : "Fuera de ANP"}
            mainLabel={s.anp_name ?? "Sin interseccion"} defaultOpen={isRisk}>
            {isRisk && (
              <>
                <Stat label="ANP" value={s.anp_name ?? "?"} accent="#f85149" />
                <Stat label="Categoria" value={s.anp_category ?? "?"} />
                <Stat label="Area superpuesta" value={`${s.overlap_area_ha?.toFixed?.(1) ?? "?"} ha`} accent="#f85149" />
                <Stat label="% del AOI en ANP" value={`${s.overlap_pct?.toFixed?.(1) ?? "?"}%`} />
                <div className="mt-2 p-2 rounded text-[10px] font-medium" style={{ backgroundColor: "#f8514922", color: "#fca5a5", border: "1px solid #f8514944" }}>
                  ALERTA: Deforestacion dentro de Area Natural Protegida — infraccion potencial bajo Art. 47 LGEEPA
                </div>
              </>
            )}
            <Stat label="Fuente" value="WDPA / CONANP" />
          </EngineCard>
        )
      })()}
    </div>
  )
}