const C = {
  bgPanel: "#161b22",
  border: "#30363d",
  text1: "#e6edf3",
  text2: "#8b949e",
} as const

const LEGEND: { color: string; label: string; dashed?: boolean }[] = [
  { color: "#ef4444", label: "Deforestacion (cambio T1\u2192T2)" },
  { color: "#166534", label: "Bosque denso (trees)" },
  { color: "#86efac", label: "Pastizal (grass)" },
  { color: "#7a87c6", label: "Manglar / veg. inundada" },
  { color: "#e49635", label: "Cultivos (crops)" },
  { color: "#dfc35a", label: "Matorral (shrub)" },
  { color: "#92400e", label: "Suelo desnudo (bare)" },
  { color: "#3b82f6", label: "Agua" },
  { color: "#6b21a8", label: "Urbano / construido" },
  { color: "#f97316", label: "Expansion urbana / fraccionamiento" },
  { color: "#b39fe1", label: "Nieve / hielo" },
  { color: "#22d3ee", label: "Estructuras" },
  { color: "#facc15", label: "Hansen forest loss (2001-2024)" },
  { color: "#dc2626", label: "Alerta GLAD-S2 (confirmada)" },
  { color: "#fb923c", label: "Alerta RADD (SAR)" },
  { color: "#8b5cf6", label: "Driver: agricultura/mineria/tala" },
  { color: "#22c55e", label: "Area Natural Protegida (ANP)", dashed: true },
  { color: "#f97316", label: "Area quemada (MODIS)" },
  { color: "#06b6d4", label: "Cambio SAR (Sentinel-1)" },
]

export default function LegendPanel() {
  return (
    <div
      className="absolute bottom-4 right-4 z-10 px-3 py-2.5 rounded-lg"
      style={{ backgroundColor: C.bgPanel + "e6", border: `1px solid ${C.border}`, backdropFilter: "blur(8px)" }}
    >
      <p className="text-[10px] font-bold uppercase tracking-wide mb-1.5" style={{ color: C.text2 }}>Leyenda</p>
      {LEGEND.map((item) => (
        <div key={item.label} className="flex items-center gap-2 py-0.5">
          <span
            className="inline-block w-3 h-3 rounded-sm flex-shrink-0"
            style={item.dashed
              ? { border: `2px dashed ${item.color}`, backgroundColor: "transparent" }
              : { backgroundColor: item.color }}
          />
          <span className="text-[11px]" style={{ color: C.text1 }}>{item.label}</span>
        </div>
      ))}
    </div>
  )
}