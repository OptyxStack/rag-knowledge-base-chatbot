import { useState, useEffect } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { tickets, admin, type Ticket } from '../api/client'
import {
  ExternalLink,
  ChevronLeft,
  ChevronRight,
  Loader2,
  Ticket as TicketIcon,
  Search,
  Filter,
  CheckCircle2,
  XCircle,
  FileDown,
  Clock,
} from 'lucide-react'

export default function TicketList() {
  const navigate = useNavigate()
  const [items, setItems] = useState<Ticket[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [filterStatus, setFilterStatus] = useState('')
  const [filterApproval, setFilterApproval] = useState('')
  const [filterQ, setFilterQ] = useState('')
  const [filterQApplied, setFilterQApplied] = useState('')
  const [ingesting, setIngesting] = useState(false)
  const [ingestResult, setIngestResult] = useState<{ path: string; count: number } | null>(null)
  const pageSize = 15

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await tickets.list(
        page,
        pageSize,
        filterStatus || undefined,
        filterApproval || undefined,
        filterQApplied || undefined
      )
      setItems(res.items)
      setTotal(res.total)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load tickets')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    setPage(1)
  }, [filterStatus, filterApproval, filterQApplied])

  const handleIngestToFile = async () => {
    setIngestResult(null)
    setIngesting(true)
    try {
      const res = await admin.ingestTicketsToFile()
      setIngestResult({ path: res.path, count: res.count })
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Ingest failed')
    } finally {
      setIngesting(false)
    }
  }

  const handleApproval = async (t: Ticket, status: 'pending' | 'approved' | 'rejected') => {
    try {
      await admin.updateTicketApproval(t.id, status)
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Update failed')
    }
  }

  useEffect(() => {
    load()
  }, [page, filterStatus, filterQApplied])

  const handleSearch = () => setFilterQApplied(filterQ)

  const totalPages = Math.ceil(total / pageSize)

  return (
    <div className="animate-slide-up">
      <header className="flex justify-between items-start mb-8">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-white">Sample conversations</h1>
        </div>
        <button
          onClick={handleIngestToFile}
          disabled={ingesting}
          className="btn-primary inline-flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {ingesting ? <Loader2 size={16} className="animate-spin" /> : <FileDown size={16} />}
          Export approved sample conversations to file
        </button>
      </header>
      {ingestResult && (
        <div className="mb-5 p-3.5 rounded-xl bg-emerald-500/10 border border-emerald-500/20 text-emerald-300 text-sm animate-fade-in">
          Exported {ingestResult.count} sample conversation(s) to {ingestResult.path}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2.5 mb-5">
        <div className="relative flex-1 min-w-[200px] max-w-xs">
          <Search size={15} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-zinc-600" />
          <input
            type="text"
            placeholder="Search subject, content..."
            value={filterQ}
            onChange={(e) => setFilterQ(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
            className="w-full pl-9 pr-4 py-2.5 rounded-xl input-glass text-sm"
          />
        </div>
        <select
          value={filterStatus}
          onChange={(e) => setFilterStatus(e.target.value)}
          aria-label="Filter by status"
          className="px-4 py-2.5 rounded-xl input-glass text-sm"
        >
          <option value="">All statuses</option>
          <option value="Open">Open</option>
          <option value="Answered">Answered</option>
          <option value="Customer-Reply">Customer-Reply</option>
          <option value="Closed">Closed</option>
          <option value="In Progress">In Progress</option>
        </select>
        <select
          value={filterApproval}
          onChange={(e) => setFilterApproval(e.target.value)}
          aria-label="Filter by approval"
          className="px-4 py-2.5 rounded-xl input-glass text-sm"
        >
          <option value="">All approval</option>
          <option value="pending">Pending</option>
          <option value="approved">Approved</option>
          <option value="rejected">Rejected</option>
        </select>
        <button
          className="btn-ghost inline-flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium"
          onClick={handleSearch}
        >
          <Filter size={15} />
          Filter
        </button>
      </div>

      {error && (
        <div className="flex items-center gap-2 p-3.5 rounded-xl mb-5 bg-danger/10 border border-danger/20 text-red-300 text-sm animate-fade-in">
          {error}
        </div>
      )}

      <div className="glass rounded-2xl overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center gap-3 py-20 text-zinc-500">
            <Loader2 size={20} className="animate-spin-slow text-accent" />
            <span className="text-sm">Loading tickets...</span>
          </div>
        ) : items.length === 0 ? (
          <div className="flex flex-col items-center py-20 text-zinc-500">
            <div className="w-16 h-16 rounded-2xl glass-accent flex items-center justify-center mb-5 glow-sm">
              <TicketIcon size={28} className="text-violet-400" />
            </div>
            <p className="font-semibold text-zinc-300 mb-1.5">No sample conversations yet</p>
            <p className="text-sm">Crawl from Crawl page to add data</p>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/[0.04]">
                <th className="px-5 py-3.5 text-left text-zinc-500 font-medium text-xs uppercase tracking-wider">ID</th>
                <th className="px-5 py-3.5 text-left text-zinc-500 font-medium text-xs uppercase tracking-wider">Subject</th>
                <th className="px-5 py-3.5 text-left text-zinc-500 font-medium text-xs uppercase tracking-wider">Status</th>
                <th className="px-5 py-3.5 text-left text-zinc-500 font-medium text-xs uppercase tracking-wider">Priority</th>
                <th className="px-5 py-3.5 text-left text-zinc-500 font-medium text-xs uppercase tracking-wider">Customer</th>
                <th className="px-5 py-3.5 text-left text-zinc-500 font-medium text-xs uppercase tracking-wider">Approval</th>
                <th className="px-5 py-3.5 text-left text-zinc-500 font-medium text-xs uppercase tracking-wider">Updated</th>
                <th className="px-5 py-3.5 text-right text-zinc-500 font-medium text-xs uppercase tracking-wider">Actions</th>
              </tr>
            </thead>
            <tbody>
              {items.map((t) => (
                <tr
                  key={t.id}
                  className="border-b border-white/[0.03] last:border-b-0 hover:bg-white/[0.02] transition-colors duration-200 cursor-pointer group"
                  onClick={() => navigate(`/tickets/${t.id}`)}
                >
                  <td className="px-5 py-4">
                    <code className="text-xs text-violet-400 bg-violet-500/10 px-2 py-1 rounded-lg font-mono">
                      {t.external_id || t.id.slice(0, 8)}
                    </code>
                  </td>
                  <td className="px-5 py-4 max-w-[240px]">
                    <span className="truncate block text-zinc-300" title={t.subject}>
                      {t.subject || '(No subject)'}
                    </span>
                  </td>
                  <td className="px-5 py-4">
                    <StatusBadge status={t.status} />
                  </td>
                  <td className="px-5 py-4 text-zinc-400">{t.priority || '-'}</td>
                  <td className="px-5 py-4 text-zinc-400">
                    {t.name || t.email || '-'}
                  </td>
                  <td className="px-5 py-4">
                    <ApprovalBadge status={t.approval_status} />
                    <div className="flex gap-1 mt-1.5" onClick={(e) => e.stopPropagation()}>
                      {t.approval_status !== 'approved' && (
                        <button
                          type="button"
                          onClick={(e) => { e.stopPropagation(); handleApproval(t, 'approved') }}
                          className="p-1 rounded-lg text-emerald-400 hover:bg-emerald-500/15 transition-colors"
                          title="Approve"
                        >
                          <CheckCircle2 size={14} />
                        </button>
                      )}
                      {t.approval_status !== 'rejected' && (
                        <button
                          type="button"
                          onClick={(e) => { e.stopPropagation(); handleApproval(t, 'rejected') }}
                          className="p-1 rounded-lg text-red-400 hover:bg-red-500/15 transition-colors"
                          title="Reject"
                        >
                          <XCircle size={14} />
                        </button>
                      )}
                      {t.approval_status !== 'pending' && (
                        <button
                          type="button"
                          onClick={(e) => { e.stopPropagation(); handleApproval(t, 'pending') }}
                          className="p-1 rounded-lg text-zinc-500 hover:bg-white/[0.05] transition-colors"
                          title="Pending"
                        >
                          <Clock size={14} />
                        </button>
                      )}
                    </div>
                  </td>
                  <td className="px-5 py-4 text-zinc-400">
                    {t.updated_at
                      ? new Date(t.updated_at).toLocaleDateString('en-US', {
                          month: 'short',
                          day: 'numeric',
                          year: 'numeric',
                        })
                      : '-'}
                  </td>
                  <td className="px-5 py-4">
                    <div className="flex items-center justify-end gap-1.5 opacity-0 group-hover:opacity-100 transition-all duration-200">
                      <Link
                        to={`/tickets/${t.id}`}
                        className="p-2 rounded-lg text-zinc-500 hover:text-white hover:bg-white/[0.06] transition-colors"
                        onClick={(e) => e.stopPropagation()}
                        title="View details"
                      >
                        <ExternalLink size={14} />
                      </Link>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-between mt-5">
          <span className="text-sm text-zinc-500">
            {total} sample conversation(s) · page {page} / {totalPages}
          </span>
          <div className="flex items-center gap-1">
            <button
              type="button"
              title="Previous page"
              aria-label="Previous page"
              className="p-2 rounded-xl text-zinc-500 hover:text-white hover:bg-white/[0.05] disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
              disabled={page <= 1}
              onClick={() => setPage((p) => p - 1)}
            >
              <ChevronLeft size={18} />
            </button>
            {Array.from({ length: Math.min(totalPages, 5) }, (_, i) => {
              const p = page <= 3 ? i + 1 : page + i - 2
              if (p < 1 || p > totalPages) return null
              return (
                <button
                  key={p}
                  className={`w-9 h-9 rounded-xl text-sm font-medium transition-all duration-200
                    ${p === page
                      ? 'btn-primary'
                      : 'text-zinc-500 hover:text-white hover:bg-white/[0.05]'
                    }`}
                  onClick={() => setPage(p)}
                >
                  {p}
                </button>
              )
            })}
            <button
              type="button"
              title="Next page"
              aria-label="Next page"
              className="p-2 rounded-xl text-zinc-500 hover:text-white hover:bg-white/[0.05] disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
              disabled={page >= totalPages}
              onClick={() => setPage((p) => p + 1)}
            >
              <ChevronRight size={18} />
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

function ApprovalBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    pending: 'bg-amber-500/10 text-amber-400 border-amber-500/15',
    approved: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/15',
    rejected: 'bg-red-500/10 text-red-400 border-red-500/15',
  }
  const labels: Record<string, string> = {
    pending: 'Pending',
    approved: 'Approved',
    rejected: 'Rejected',
  }
  const cls = styles[status] || 'bg-white/[0.03] text-zinc-400 border-white/[0.06]'
  return (
    <span className={`inline-flex px-2.5 py-1 text-xs font-medium rounded-lg border ${cls}`}>
      {labels[status] || status}
    </span>
  )
}

function StatusBadge({ status }: { status: string }) {
  const s = (status || '').toLowerCase()
  const styles: Record<string, string> = {
    open: 'bg-amber-500/10 text-amber-400 border-amber-500/15',
    answered: 'bg-blue-500/10 text-blue-400 border-blue-500/15',
    'customer-reply': 'bg-emerald-500/10 text-emerald-400 border-emerald-500/15',
    closed: 'bg-zinc-500/10 text-zinc-400 border-zinc-500/15',
    'in progress': 'bg-cyan-500/10 text-cyan-400 border-cyan-500/15',
  }
  const cls = styles[s] || 'bg-white/[0.03] text-zinc-400 border-white/[0.06]'
  return (
    <span className={`inline-flex px-2.5 py-1 text-xs font-medium rounded-lg border ${cls}`}>
      {status || 'N/A'}
    </span>
  )
}
