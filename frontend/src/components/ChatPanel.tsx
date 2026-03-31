import { useState, useCallback, useRef, useEffect } from "react"
import { API_BASE_URL } from "../config"

/* ── Design tokens ── */
const C = {
  bgBase: "#0d1117", bgPanel: "#161b22", bgCard: "#21262d",
  border: "#30363d", green: "#2ea043", text1: "#e6edf3",
  text2: "#8b949e", blue: "#58a6ff", orange: "#f0883e",
} as const

interface ChatMessage {
  role: "user" | "assistant" | "system"
  text: string
  model?: string
  mode?: string
  ts: number
}

const SUGGESTED_QUESTIONS = [
  "¿Cuál es el resumen del análisis?",
  "¿Cuánta deforestación se detectó?",
  "¿Cuál es el impacto en CO₂ y biomasa?",
  "¿Se detectaron incendios activos?",
  "¿Qué alertas GLAD/RADD hay vigentes?",
  "¿Cuáles son las anomalías de NDVI?",
  "¿Cuál es la evaluación de riesgo?",
  "¿Qué recomendaciones hay?",
]

interface ChatPanelProps {
  jobId: string | null
  results: any | null
}

export default function ChatPanel({ jobId, results }: ChatPanelProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState("")
  const [loading, setLoading] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)

  /* Auto-scroll on new messages */
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" })
  }, [messages])

  const sendMessage = useCallback(async (text: string) => {
    if (!text.trim() || loading) return

    const userMsg: ChatMessage = { role: "user", text: text.trim(), ts: Date.now() }
    setMessages(prev => [...prev, userMsg])
    setInput("")
    setLoading(true)

    try {
      const res = await fetch(`${API_BASE_URL}/api/chat/query`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: text.trim(),
          job_id: jobId,
          job_results: results,
        }),
      })

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: "Error desconocido" }))
        throw new Error(err.detail || `HTTP ${res.status}`)
      }

      const data = await res.json()
      const botMsg: ChatMessage = {
        role: "assistant",
        text: data.answer,
        model: data.model,
        mode: data.mode,
        ts: Date.now(),
      }
      setMessages(prev => [...prev, botMsg])
    } catch (err: any) {
      setMessages(prev => [...prev, {
        role: "system",
        text: `Error: ${err.message}`,
        ts: Date.now(),
      }])
    } finally {
      setLoading(false)
    }
  }, [jobId, results, loading])

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: 8 }}>
      {/* Status bar */}
      <div style={{ background: C.bgCard, borderRadius: 8, padding: "8px 12px",
                     border: `1px solid ${C.border}`, display: "flex", alignItems: "center",
                     gap: 8, fontSize: 12, color: C.text2 }}>
        <span style={{ width: 8, height: 8, borderRadius: "50%",
                       background: C.green,
                       display: "inline-block" }} />
        <span>
          APEX IA local activa · Sin APIs externas
        </span>
      </div>

      {/* Messages area */}
      <div ref={scrollRef}
           style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column",
                    gap: 8, padding: "4px 0" }}>
        {messages.length === 0 && (
          <div style={{ textAlign: "center", color: C.text2, padding: 24 }}>
            <div style={{ fontSize: 14, marginBottom: 12 }}>
              Preguntale a APEX IA sobre los resultados del analisis
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, justifyContent: "center" }}>
              {SUGGESTED_QUESTIONS.map(q => (
                <button key={q} onClick={() => sendMessage(q)}
                        style={{ padding: "4px 10px", borderRadius: 12,
                                 background: C.bgCard, border: `1px solid ${C.border}`,
                                 color: C.blue, cursor: "pointer", fontSize: 12 }}>
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <div key={i} style={{
            alignSelf: msg.role === "user" ? "flex-end" : "flex-start",
            maxWidth: "85%",
            background: msg.role === "user" ? "#1f6feb33" : msg.role === "system" ? "#f8514933" : C.bgCard,
            borderRadius: 10,
            padding: "8px 12px",
            border: `1px solid ${msg.role === "system" ? "#f8514966" : C.border}`,
          }}>
            <div style={{ fontSize: 13, color: C.text1, whiteSpace: "pre-wrap", lineHeight: 1.5 }}>
              {msg.text}
            </div>
            {msg.mode && (
              <div style={{ fontSize: 10, color: C.text2, marginTop: 4, textAlign: "right" }}>
                {msg.mode === "local" ? "APEX IA Local" : "Respuesta contextual"} · {new Date(msg.ts).toLocaleTimeString()}
              </div>
            )}
          </div>
        ))}

        {loading && (
          <div style={{ alignSelf: "flex-start", color: C.text2, fontSize: 13, padding: "4px 12px" }}>
            Pensando…
          </div>
        )}
      </div>

      {/* Input area */}
      <div style={{ display: "flex", gap: 8 }}>
        <input
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === "Enter" && !e.shiftKey && sendMessage(input)}
          placeholder="Escribe una pregunta sobre el análisis…"
          disabled={loading}
          style={{ flex: 1, padding: "8px 12px", borderRadius: 8,
                   background: C.bgCard, border: `1px solid ${C.border}`,
                   color: C.text1, fontSize: 13, outline: "none" }}
        />
        <button onClick={() => sendMessage(input)} disabled={loading || !input.trim()}
                style={{ padding: "8px 16px", borderRadius: 8,
                         background: C.green, color: "#fff", border: "none",
                         cursor: loading ? "not-allowed" : "pointer", fontSize: 13,
                         opacity: loading || !input.trim() ? 0.5 : 1 }}>
          Enviar
        </button>
      </div>
    </div>
  )
}
