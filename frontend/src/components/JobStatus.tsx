import { useEffect, useState, useRef } from "react"
import { API_BASE_URL } from "../config"

export default function JobStatus({ jobId, onCompleted }: { jobId: string; onCompleted?: (id: string) => void }) {
  const [status, setStatus] = useState("queued")
  const [progress, setProgress] = useState(0)
  const [step, setStep] = useState("Iniciando...")
  const [lastLog, setLastLog] = useState("")
  const [groupInfo, setGroupInfo] = useState<{ current: number; total: number } | null>(null)
  const [elapsed, setElapsed] = useState(0)
  const [logCount, setLogCount] = useState(0)
  const seenRef = useRef(0)
  const startRef = useRef(Date.now())

  useEffect(() => {
    if (!jobId) return
    seenRef.current = 0
    startRef.current = Date.now()
    setGroupInfo(null)
    setElapsed(0)
    setLogCount(0)
    console.log(`%c[APEX] Polling job ${jobId.slice(0, 8)}…`, "color: #60a5fa")
    const interval = setInterval(async () => {
      try {
        setElapsed(Math.floor((Date.now() - startRef.current) / 1000))
        const r = await fetch(`${API_BASE_URL}/api/jobs/${jobId}`)
        const d = await r.json()
        setStatus(d.status)
        setProgress(d.progress ?? 0)
        setStep(d.current_step ?? "Procesando...")

        // Detect group progress
        const groupMatch = (d.current_step ?? "").match(/Grupo (\d+)\/(\d+)/)
        if (groupMatch) {
          setGroupInfo({ current: parseInt(groupMatch[1]), total: parseInt(groupMatch[2]) })
        }

        // Stream server logs to browser console
        const logs: string[] = d.logs ?? []
        for (let i = seenRef.current; i < logs.length; i++) {
          const msg = logs[i]
          const style = msg.includes("✓") || msg.includes("✅")
            ? "color: #4ade80; font-weight: bold"
            : msg.includes("❌") || msg.includes("⚠")
            ? "color: #f87171; font-weight: bold"
            : msg.includes("raster bounds") || msg.includes("@")
            ? "color: #93c5fd"
            : "color: #a78bfa"
          console.log(`%c[APEX-Server] ${msg}`, style)
        }
        if (logs.length > seenRef.current) {
          setLastLog(logs[logs.length - 1])
          setLogCount(logs.length)
        }
        seenRef.current = logs.length

        if (d.status === "completed" || d.status === "failed") {
          console.log(
            `%c[APEX] ${d.status === "completed" ? "✅" : "❌"} Job ${jobId.slice(0, 8)} → ${d.status} (${logs.length} log entries)`,
            d.status === "completed" ? "color: #4ade80; font-weight: bold" : "color: #f87171; font-weight: bold"
          )
          clearInterval(interval)
          if (d.status === "completed") onCompleted?.(jobId)
        }
      } catch (err: any) {
        console.warn(`%c[APEX] Polling error for ${jobId.slice(0, 8)}:`, "color: #d29922", err?.message || err)
      }
    }, 1000)
    return () => clearInterval(interval)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId])

  const color = status === "failed" ? "bg-red-500" : status === "completed" ? "bg-green-500" : "bg-blue-500"
  const fmtElapsed = elapsed >= 60 ? `${Math.floor(elapsed / 60)}m${elapsed % 60}s` : `${elapsed}s`

  return (
    <div className="mt-3 border-t border-gray-700 pt-2">
      <div className="flex items-center gap-2 mb-1">
        {status !== "completed" && status !== "failed" && (
          <div className="w-3 h-3 rounded-full border-2 border-blue-400 border-t-transparent animate-spin flex-shrink-0" />
        )}
        {status === "completed" && <span className="text-green-400 text-xs flex-shrink-0">✓</span>}
        {status === "failed" && <span className="text-red-400 text-xs flex-shrink-0">✗</span>}
        <span className="text-xs text-gray-300 truncate flex-1">{step}</span>
        <span className="text-[10px] text-gray-500 flex-shrink-0 tabular-nums">{fmtElapsed}</span>
      </div>
      <div className="w-full bg-gray-700 rounded-full h-2">
        <div className={`${color} h-2 rounded-full transition-all duration-500`} style={{ width: `${progress}%` }} />
      </div>
      {/* Group progress indicator */}
      {groupInfo && groupInfo.total > 1 && (
        <div className="flex gap-0.5 mt-1">
          {Array.from({ length: groupInfo.total }, (_, i) => (
            <div
              key={i}
              className={`h-1.5 flex-1 rounded-sm transition-colors duration-300 ${
                i < groupInfo.current ? "bg-green-500" : i === groupInfo.current ? "bg-blue-400 animate-pulse" : "bg-gray-600"
              }`}
            />
          ))}
        </div>
      )}
      <div className="flex justify-between mt-0.5">
        <p className="text-[10px] text-gray-500 truncate max-w-[70%]">{lastLog}</p>
        <p className="text-[10px] text-gray-400 tabular-nums">{progress}% · {logCount} logs</p>
      </div>
      {status === "failed" && <p className="text-xs text-red-400 mt-1">Error en el analisis</p>}
    </div>
  )
}