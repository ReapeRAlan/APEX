import { useState, type FormEvent } from "react"
import { API_BASE_URL } from "../config"

const C = {
  bgBase: "#0d1117",
  bgPanel: "#161b22",
  border: "#30363d",
  text1: "#e6edf3",
  text2: "#8b949e",
  accent: "#58a6ff",
  green: "#3fb950",
  red: "#f85149",
} as const

interface LoginPageProps {
  onLogin: (token: string, user: { email: string; role: string; full_name: string }) => void
}

export default function LoginPage({ onLogin }: LoginPageProps) {
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (!email || !password) return
    setLoading(true)
    setError(null)

    try {
      const res = await fetch(`${API_BASE_URL}/api/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        setError(body.detail || `Error ${res.status}`)
        return
      }
      const data = await res.json()
      localStorage.setItem("apex_token", data.access_token)
      localStorage.setItem("apex_user", JSON.stringify(data.user))
      onLogin(data.access_token, data.user)
    } catch {
      setError("No se pudo conectar al servidor")
    } finally {
      setLoading(false)
    }
  }

  return (
    <div
      className="flex items-center justify-center h-screen w-screen"
      style={{ backgroundColor: C.bgBase }}
    >
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm rounded-xl p-8 shadow-2xl"
        style={{ backgroundColor: C.bgPanel, border: `1px solid ${C.border}` }}
      >
        {/* Logo / Title */}
        <div className="text-center mb-8">
          <h1 className="text-2xl font-bold" style={{ color: C.text1 }}>
            APEX
          </h1>
          <p className="text-xs mt-1" style={{ color: C.text2 }}>
            Análisis Predictivo de Ecosistemas con IA
          </p>
        </div>

        {/* Username */}
        <label className="block text-xs font-medium mb-1" style={{ color: C.text2 }}>
          Correo electrónico
        </label>
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="w-full rounded-md px-3 py-2 text-sm mb-4 outline-none focus:ring-2"
          style={{
            backgroundColor: C.bgBase,
            border: `1px solid ${C.border}`,
            color: C.text1,
            // @ts-ignore -- ring color
            "--tw-ring-color": C.accent,
          } as React.CSSProperties}
          placeholder="usuario@profepa.gob.mx"
          autoComplete="email"
        />

        {/* Password */}
        <label className="block text-xs font-medium mb-1" style={{ color: C.text2 }}>
          Contraseña
        </label>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="w-full rounded-md px-3 py-2 text-sm mb-6 outline-none focus:ring-2"
          style={{
            backgroundColor: C.bgBase,
            border: `1px solid ${C.border}`,
            color: C.text1,
          } as React.CSSProperties}
          placeholder="••••••••"
          autoComplete="current-password"
        />

        {/* Error */}
        {error && (
          <p className="text-xs mb-4 text-center" style={{ color: C.red }}>
            {error}
          </p>
        )}

        {/* Submit */}
        <button
          type="submit"
          disabled={loading || !email || !password}
          className="w-full rounded-md py-2 text-sm font-medium transition-opacity disabled:opacity-40"
          style={{ backgroundColor: C.accent, color: "#fff" }}
        >
          {loading ? "Autenticando..." : "Iniciar Sesión"}
        </button>
      </form>
    </div>
  )
}
