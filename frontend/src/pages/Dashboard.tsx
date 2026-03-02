import { useState, useEffect } from 'react'
import { dashboard } from '../api/client'
import {
  BarChart3,
  Loader2,
  TrendingUp,
  Activity,
  Zap,
  MessageSquare,
  Clock,
  RefreshCw,
  Sparkles,
} from 'lucide-react'

const METRIC_ICONS: Record<string, typeof Activity> = {
  requests: Activity,
  messages: MessageSquare,
  latency: Clock,
  tokens: Zap,
  conversations: MessageSquare,
}

const CARD_STYLES = [
  { gradient: 'from-violet-500/15 via-violet-500/5 to-transparent', glow: 'rgba(124,58,237,0.12)', iconColor: 'text-violet-400', borderColor: 'rgba(124,58,237,0.15)' },
  { gradient: 'from-emerald-500/15 via-emerald-500/5 to-transparent', glow: 'rgba(16,185,129,0.12)', iconColor: 'text-emerald-400', borderColor: 'rgba(16,185,129,0.15)' },
  { gradient: 'from-amber-500/15 via-amber-500/5 to-transparent', glow: 'rgba(245,158,11,0.12)', iconColor: 'text-amber-400', borderColor: 'rgba(245,158,11,0.15)' },
  { gradient: 'from-cyan-500/15 via-cyan-500/5 to-transparent', glow: 'rgba(6,182,212,0.12)', iconColor: 'text-cyan-400', borderColor: 'rgba(6,182,212,0.15)' },
  { gradient: 'from-blue-500/15 via-blue-500/5 to-transparent', glow: 'rgba(59,130,246,0.12)', iconColor: 'text-blue-400', borderColor: 'rgba(59,130,246,0.15)' },
  { gradient: 'from-rose-500/15 via-rose-500/5 to-transparent', glow: 'rgba(244,63,94,0.12)', iconColor: 'text-rose-400', borderColor: 'rgba(244,63,94,0.15)' },
]

export default function Dashboard() {
  const [metrics, setMetrics] = useState<Record<string, number>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)

  const load = async (isRefresh = false) => {
    if (isRefresh) setRefreshing(true)
    else setLoading(true)
    setError(null)
    try {
      const res = await dashboard.stats()
      setMetrics(res.metrics || {})
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load stats')
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  const entries = Object.entries(metrics).filter(([k]) => k.startsWith('support_ai_'))

  if (loading) return (
    <div className="flex items-center justify-center gap-3 py-24 animate-fade-in">
      <Loader2 size={22} className="animate-spin-slow text-accent" />
      <span className="text-zinc-500">Loading dashboard...</span>
    </div>
  )

  if (error && entries.length === 0) return (
    <div className="animate-fade-in">
      <div className="p-3.5 rounded-xl mb-5 bg-danger/10 border border-danger/20 text-red-300 text-sm">{error}</div>
    </div>
  )

  return (
    <div className="animate-slide-up">
      <header className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-white">Dashboard</h1>
          <p className="text-sm text-zinc-500 mt-1.5">Real-time metrics from Prometheus</p>
        </div>
        <button
          className="btn-ghost inline-flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium disabled:opacity-50 transition-all"
          onClick={() => load(true)}
          disabled={refreshing}
        >
          <RefreshCw size={14} className={refreshing ? 'animate-spin-slow' : ''} />
          Refresh
        </button>
      </header>

      {error && (
        <div className="p-3.5 rounded-xl mb-5 bg-danger/10 border border-danger/20 text-red-300 text-sm animate-fade-in">
          {error}
        </div>
      )}

      {entries.length === 0 ? (
        <div className="flex flex-col items-center py-24 text-zinc-500">
          <div className="w-16 h-16 rounded-2xl glass-accent flex items-center justify-center mb-5 glow-sm">
            <BarChart3 size={30} className="text-violet-400" />
          </div>
          <p className="font-semibold text-zinc-300 mb-1.5">No metrics available</p>
          <p className="text-sm">Metrics will appear once the system starts processing requests</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
          {entries.map(([key, value], index) => {
            const name = key.replace('support_ai_', '')
            const iconKey = Object.keys(METRIC_ICONS).find((k) => name.toLowerCase().includes(k))
            const Icon = iconKey ? METRIC_ICONS[iconKey] : TrendingUp
            const style = CARD_STYLES[index % CARD_STYLES.length]

            return (
              <div
                key={key}
                className={`relative overflow-hidden rounded-2xl p-5 transition-all duration-300 card-hover`}
                style={{
                  background: `linear-gradient(135deg, ${style.glow}, transparent 60%)`,
                  border: `1px solid ${style.borderColor}`,
                  backdropFilter: 'blur(12px)',
                }}
              >
                <div className="absolute inset-0 dot-pattern opacity-30" />
                <div className="relative">
                  <div className="flex items-start justify-between mb-4">
                    <div
                      className={`w-10 h-10 rounded-xl flex items-center justify-center ${style.iconColor}`}
                      style={{ background: 'rgba(0,0,0,0.3)' }}
                    >
                      <Icon size={19} />
                    </div>
                    <Sparkles size={14} className="text-zinc-600" />
                  </div>
                  <div className="text-3xl font-bold tracking-tight text-white mb-1.5">
                    {formatMetricValue(value)}
                  </div>
                  <div className="text-xs font-medium text-zinc-500 capitalize">
                    {name.replace(/_/g, ' ')}
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

function formatMetricValue(value: number): string {
  if (typeof value !== 'number') return String(value)
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`
  if (Number.isInteger(value)) return value.toLocaleString()
  return value.toFixed(2)
}
