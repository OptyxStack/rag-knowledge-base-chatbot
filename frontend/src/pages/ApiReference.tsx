import { useState, useEffect } from 'react'
import { BookOpen, ChevronDown, ChevronRight, Copy, Loader2 } from 'lucide-react'

const API_BASE =
  import.meta.env.VITE_API_BASE ||
  (import.meta.env.DEV ? 'http://localhost:8000/v1' : '/v1')
const OPENAPI_URL = API_BASE.replace(/\/v1\/?$/, '') + '/openapi.json'
const STORAGE_KEY = 'support_ai_token'
const API_KEY = import.meta.env.VITE_API_KEY || import.meta.env.VITE_ADMIN_API_KEY

type OpenAPISpec = {
  paths?: Record<string, Record<string, {
    summary?: string
    description?: string
    parameters?: Array<{ name: string; in: string; required?: boolean; schema?: { type?: string } }>
    requestBody?: { content?: Record<string, { schema?: object }> }
    responses?: Record<string, { description?: string; content?: Record<string, { schema?: object }> }>
  }>>
  info?: { title?: string; version?: string }
}

type JsonSchemaContainer = {
  content?: Record<string, { schema?: object }>
}

type EndpointSpec = {
  summary?: string
  description?: string
  parameters?: unknown[]
  requestBody?: unknown
  responses?: unknown
}

const METHOD_COLORS: Record<string, string> = {
  get: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30',
  post: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
  put: 'bg-amber-500/20 text-amber-400 border-amber-500/30',
  patch: 'bg-orange-500/20 text-orange-400 border-orange-500/30',
  delete: 'bg-red-500/20 text-red-400 border-red-500/30',
}

function formatJson(obj: unknown): string {
  return JSON.stringify(obj, null, 2)
}

function EndpointCard({
  path,
  method,
  spec,
}: {
  path: string
  method: string
  spec: EndpointSpec
}) {
  const [open, setOpen] = useState(false)
  const methodColor = METHOD_COLORS[method.toLowerCase()] || 'bg-zinc-500/20 text-zinc-400'

  const JSON_MIME = 'application/json'
  const getJsonSchema = (obj: JsonSchemaContainer | undefined) =>
    obj?.content?.[JSON_MIME]?.schema

  const reqBody = spec.requestBody as JsonSchemaContainer | undefined
  const bodySchema = getJsonSchema(reqBody)

  const responses =
    spec.responses && typeof spec.responses === 'object'
      ? (spec.responses as Record<string, JsonSchemaContainer>)
      : undefined
  const res200 = responses?.['200']
  const successResponse = getJsonSchema(res200)

  const exampleRequest = bodySchema
    ? (() => {
        if (path.includes('conversations') && method === 'POST') {
          return { source_type: 'ticket', source_id: 'T123', metadata: {} }
        }
        if (path.includes('messages') && method === 'POST') {
          return { content: 'How do I reset my password?' }
        }
        if (path.includes('login')) {
          return { username: 'admin', password: '••••••••' }
        }
        if (path.includes('documents') && method === 'POST') {
          return { url: 'https://example.com/doc', title: 'Doc title', doc_type: 'faq' }
        }
        return bodySchema
      })()
    : null

  return (
    <div
      className="rounded-xl overflow-hidden"
      style={{
        background: 'rgba(255,255,255,0.03)',
        border: '1px solid rgba(255,255,255,0.06)',
      }}
    >
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-3 px-4 py-3.5 text-left hover:bg-white/[0.02] transition-colors"
      >
        {open ? <ChevronDown size={18} className="text-zinc-500 shrink-0" /> : <ChevronRight size={18} className="text-zinc-500 shrink-0" />}
        <span className={`px-2 py-0.5 rounded text-xs font-semibold uppercase border ${methodColor}`}>
          {method}
        </span>
        <code className="text-sm text-zinc-300 font-mono">{path}</code>
        <span className="text-zinc-500 text-sm truncate ml-auto">
          {spec.summary || spec.description || ''}
        </span>
      </button>
      {open && (
        <div className="px-4 pb-4 pt-0 space-y-4 border-t border-white/5">
          {(spec.description || spec.summary) && (
            <p className="text-sm text-zinc-400 pt-3">{spec.description || spec.summary}</p>
          )}
          <div className="grid gap-4 md:grid-cols-2">
            {exampleRequest && (
              <div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-medium text-zinc-500 uppercase tracking-wider">Request body</span>
                  <button
                    onClick={() => navigator.clipboard.writeText(formatJson(exampleRequest))}
                    className="p-1 rounded text-zinc-500 hover:text-white hover:bg-white/5"
                    title="Copy"
                  >
                    <Copy size={14} />
                  </button>
                </div>
                <pre className="p-3 rounded-lg bg-black/30 text-xs text-zinc-300 font-mono overflow-x-auto max-h-48 overflow-y-auto">
                  {formatJson(exampleRequest)}
                </pre>
              </div>
            )}
            {successResponse && (
              <div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-medium text-zinc-500 uppercase tracking-wider">Response (200)</span>
                  <button
                    onClick={() => navigator.clipboard.writeText(formatJson(successResponse))}
                    className="p-1 rounded text-zinc-500 hover:text-white hover:bg-white/5"
                    title="Copy"
                  >
                    <Copy size={14} />
                  </button>
                </div>
                <pre className="p-3 rounded-lg bg-black/30 text-xs text-zinc-300 font-mono overflow-x-auto max-h-48 overflow-y-auto">
                  {formatJson(successResponse)}
                </pre>
              </div>
            )}
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs text-zinc-400">Headers:</span>
            <code className="text-xs px-2 py-1 rounded bg-white/5 text-zinc-400">
              X-API-Key: {import.meta.env.VITE_API_KEY ? '••••••••' : 'your-api-key'}
            </code>
            <code className="text-xs px-2 py-1 rounded bg-white/5 text-zinc-400">
              Content-Type: application/json
            </code>
          </div>
        </div>
      )}
    </div>
  )
}

export default function ApiReference() {
  const [spec, setSpec] = useState<OpenAPISpec | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const loadSpec = async () => {
      setLoading(true)
      setError(null)
      try {
        const token = localStorage.getItem(STORAGE_KEY)
        const res = await fetch(OPENAPI_URL, {
          headers: {
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
            ...(API_KEY ? { 'X-API-Key': API_KEY } : {}),
          },
        })
        const text = await res.text()

        if (!res.ok) {
          throw new Error(`Failed to load API spec: HTTP ${res.status}`)
        }

        try {
          const data = JSON.parse(text) as OpenAPISpec
          if (!cancelled) setSpec(data)
        } catch {
          const preview = text.trim().slice(0, 120)
          throw new Error(
            `OpenAPI endpoint did not return JSON. Received: ${preview || 'empty response'}`
          )
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : 'Failed to load API spec')
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    void loadSpec()
    return () => { cancelled = true }
  }, [])

  if (loading) {
    return (
      <div className="flex items-center justify-center gap-3 py-24 animate-fade-in">
        <Loader2 size={22} className="animate-spin text-violet-400" />
        <span className="text-zinc-500">Loading API reference...</span>
      </div>
    )
  }

  if (error || !spec?.paths) {
    return (
      <div className="animate-fade-in">
        <div className="p-4 rounded-xl bg-red-500/10 border border-red-500/20 text-red-300 text-sm">
          {error || 'Invalid OpenAPI spec'}
        </div>
      </div>
    )
  }

  const paths = Object.entries(spec.paths).sort(([a], [b]) => a.localeCompare(b))
  const grouped = paths.reduce<Record<string, [string, string, Record<string, unknown>][]>>((acc, [path, methods]) => {
    const parts = path.split('/').filter(Boolean)
    const tag = parts[1] || parts[0] || 'other'
    if (!acc[tag]) acc[tag] = []
    for (const [method, mSpec] of Object.entries(methods)) {
      if (['get', 'post', 'put', 'patch', 'delete'].includes(method.toLowerCase())) {
        acc[tag].push([path, method, mSpec as Record<string, unknown>])
      }
    }
    return acc
  }, {})

  return (
    <div className="space-y-6 animate-fade-in">
      <header>
        <h1 className="text-xl font-semibold text-white flex items-center gap-2">
          <BookOpen size={22} />
          API Reference
        </h1>
        <p className="text-sm text-zinc-500 mt-1">
          {spec.info?.title || 'Support AI'} · Base URL: <code className="text-zinc-400">{API_BASE}</code>
        </p>
      </header>

      <div className="space-y-8">
        {Object.entries(grouped).map(([tag, endpoints]) => (
          <section key={tag}>
            <h2 className="text-sm font-medium text-zinc-400 uppercase tracking-wider mb-4">
              {tag}
            </h2>
            <div className="space-y-3">
              {endpoints.map(([path, method, mSpec]) => (
                <EndpointCard key={`${method}-${path}`} path={path} method={method} spec={mSpec} />
              ))}
            </div>
          </section>
        ))}
      </div>
    </div>
  )
}
