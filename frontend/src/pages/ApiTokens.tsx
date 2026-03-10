import { useState, useEffect } from 'react'
import { Key, Plus, Trash2, Copy, Check } from 'lucide-react'

const API_BASE =
  import.meta.env.VITE_API_BASE ||
  (import.meta.env.DEV ? 'http://localhost:8000/v1' : '/v1')
const STORAGE_KEY = 'support_ai_token'

interface ApiToken {
  id: string
  name: string
  token_prefix: string
  scopes: string
  expires_at: string | null
  last_used_at: string | null
  created_at: string
}

interface ApiTokenCreateResponse {
  id: string
  name: string
  token: string
  token_prefix: string
  scopes: string
  expires_at: string | null
  created_at: string
}

export default function ApiTokens() {
  const [tokens, setTokens] = useState<ApiToken[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  const [newToken, setNewToken] = useState<ApiTokenCreateResponse | null>(null)
  const [copied, setCopied] = useState(false)

  const fetchTokens = async () => {
    setLoading(true)
    setError(null)
    try {
      const token = localStorage.getItem(STORAGE_KEY)
      const res = await fetch(`${API_BASE}/auth/tokens`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error((await res.json()).detail || 'Failed to load tokens')
      const data = (await res.json()) as ApiToken[]
      setTokens(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load tokens')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchTokens()
  }, [])

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!newName.trim()) return
    setCreating(true)
    setError(null)
    setNewToken(null)
    try {
      const token = localStorage.getItem(STORAGE_KEY)
      const res = await fetch(`${API_BASE}/auth/tokens`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ name: newName.trim() }),
      })
      if (!res.ok) throw new Error((await res.json()).detail || 'Failed to create token')
      const data = (await res.json()) as ApiTokenCreateResponse
      setNewToken(data)
      setNewName('')
      fetchTokens()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to create token')
    } finally {
      setCreating(false)
    }
  }

  const handleRevoke = async (id: string) => {
    if (!confirm('Revoke this token? It will stop working immediately.')) return
    try {
      const token = localStorage.getItem(STORAGE_KEY)
      const res = await fetch(`${API_BASE}/auth/tokens/${id}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error((await res.json()).detail || 'Failed to revoke')
      fetchTokens()
      if (newToken?.id === id) setNewToken(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to revoke')
    }
  }

  const copyToken = () => {
    if (!newToken?.token) return
    navigator.clipboard.writeText(newToken.token)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const exportToken = () => {
    if (!newToken?.token) return
    const blob = new Blob(
      [
        `# API Token - ${newToken.name}\n`,
        `# Created: ${newToken.created_at}\n`,
        `# Use in headers: X-API-Key: <token>\n\n`,
        newToken.token,
      ],
      { type: 'text/plain' }
    )
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `api-token-${newToken.name.replace(/\s+/g, '-')}.txt`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-white flex items-center gap-2">
          <Key size={22} />
          API Tokens
        </h1>
        <p className="text-sm text-zinc-500 mt-1">
          Create tokens for programmatic access. Use <code className="text-zinc-400">X-API-Key</code> header.
        </p>
      </div>

      {newToken && (
        <div
          className="rounded-xl p-4 space-y-3"
          style={{
            background: 'rgba(34,197,94,0.1)',
            border: '1px solid rgba(34,197,94,0.3)',
          }}
        >
          <div className="font-medium text-green-400">Token created – copy it now. It won't be shown again.</div>
          <div className="flex items-center gap-2 flex-wrap">
            <code className="flex-1 min-w-0 px-3 py-2 rounded-lg bg-black/30 text-green-300 text-sm font-mono truncate">
              {newToken.token}
            </code>
            <button
              onClick={copyToken}
              className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-white/5 hover:bg-white/10 text-sm"
            >
              {copied ? <Check size={16} /> : <Copy size={16} />}
              {copied ? 'Copied' : 'Copy'}
            </button>
            <button
              onClick={exportToken}
              className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-white/5 hover:bg-white/10 text-sm"
            >
              Export
            </button>
          </div>
        </div>
      )}

      <form onSubmit={handleCreate} className="flex gap-2">
        <input
          type="text"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          placeholder="Token name (e.g. CI/CD, Integration)"
          className="flex-1 px-4 py-2 rounded-xl bg-white/5 border border-white/10 text-white placeholder-zinc-500 focus:outline-none focus:ring-2 focus:ring-violet-500/50"
        />
        <button
          type="submit"
          disabled={creating || !newName.trim()}
          className="flex items-center gap-2 px-4 py-2 rounded-xl font-medium text-white disabled:opacity-50"
          style={{ background: 'linear-gradient(135deg, #7c3aed 0%, #6d28d9 100%)' }}
        >
          <Plus size={18} />
          {creating ? 'Creating...' : 'Create'}
        </button>
      </form>

      {error && (
        <div className="text-sm text-red-400 bg-red-500/10 rounded-lg px-3 py-2">{error}</div>
      )}

      <div
        className="rounded-xl overflow-hidden"
        style={{
          background: 'rgba(255,255,255,0.03)',
          border: '1px solid rgba(255,255,255,0.06)',
        }}
      >
        {loading ? (
          <div className="p-8 text-center text-zinc-500">Loading...</div>
        ) : tokens.length === 0 ? (
          <div className="p-8 text-center text-zinc-500">No tokens yet. Create one above.</div>
        ) : (
          <table className="w-full">
            <thead>
              <tr className="border-b border-white/5">
                <th className="text-left py-3 px-4 text-xs font-medium text-zinc-500">Name</th>
                <th className="text-left py-3 px-4 text-xs font-medium text-zinc-500">Prefix</th>
                <th className="text-left py-3 px-4 text-xs font-medium text-zinc-500">Created</th>
                <th className="w-12" />
              </tr>
            </thead>
            <tbody>
              {tokens.map((t) => (
                <tr key={t.id} className="border-b border-white/5 last:border-0">
                  <td className="py-3 px-4 text-white">{t.name}</td>
                  <td className="py-3 px-4 text-zinc-400 font-mono text-sm">{t.token_prefix}</td>
                  <td className="py-3 px-4 text-zinc-500 text-sm">
                    {new Date(t.created_at).toLocaleDateString()}
                  </td>
                  <td className="py-3 px-4">
                    <button
                      onClick={() => handleRevoke(t.id)}
                      className="p-1.5 rounded-lg text-zinc-500 hover:text-red-400 hover:bg-red-500/10"
                      title="Revoke"
                    >
                      <Trash2 size={16} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
