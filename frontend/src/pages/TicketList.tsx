import { useEffect, useState, type CSSProperties, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { admin, tickets, type Ticket } from '../api/client'
import {
  ArrowUpRight,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  Clock3,
  ExternalLink,
  FileDown,
  Filter,
  Loader2,
  Mail,
  MessageSquareText,
  RefreshCw,
  RotateCcw,
  Search,
  Ticket as TicketIcon,
  UserRound,
  X,
  XCircle,
} from 'lucide-react'

const DEFAULT_PAGE_SIZE = 20
const PAGE_SIZE_OPTIONS = [10, 20, 50, 100]

const descriptionPreviewStyle: CSSProperties = {
  display: '-webkit-box',
  WebkitLineClamp: 3,
  WebkitBoxOrient: 'vertical',
  overflow: 'hidden',
}

export default function TicketList() {
  const navigate = useNavigate()
  const [items, setItems] = useState<Ticket[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE)
  const [pageInput, setPageInput] = useState('1')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [filterStatus, setFilterStatus] = useState('')
  const [filterApproval, setFilterApproval] = useState('')
  const [filterQ, setFilterQ] = useState('')
  const [filterQApplied, setFilterQApplied] = useState('')
  const [ingesting, setIngesting] = useState(false)
  const [ingestResult, setIngestResult] = useState<{ path: string; count: number } | null>(null)

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
      setError(e instanceof Error ? e.message : 'Failed to load sample conversations')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [page, pageSize, filterStatus, filterApproval, filterQApplied])

  useEffect(() => {
    setPageInput(String(page))
  }, [page])

  const handleSearch = () => {
    setFilterQApplied(filterQ.trim())
    setPage(1)
  }

  const handleResetFilters = () => {
    setFilterStatus('')
    setFilterApproval('')
    setFilterQ('')
    setFilterQApplied('')
    setPageSize(DEFAULT_PAGE_SIZE)
    setPage(1)
  }

  const handleRefresh = async () => {
    await load()
  }

  const handleIngestToFile = async () => {
    setIngestResult(null)
    setIngesting(true)
    try {
      const res = await admin.ingestTicketsToFile()
      setIngestResult({ path: res.path, count: res.count })
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Export failed')
    } finally {
      setIngesting(false)
    }
  }

  const handleApproval = async (ticket: Ticket, status: 'pending' | 'approved' | 'rejected') => {
    try {
      await admin.updateTicketApproval(ticket.id, status)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Approval update failed')
    }
  }

  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  const rangeStart = total === 0 ? 0 : (page - 1) * pageSize + 1
  const rangeEnd = total === 0 ? 0 : Math.min(page * pageSize, total)
  const activeFilterCount = [filterQApplied, filterStatus, filterApproval].filter(Boolean).length
  const visiblePages = buildVisiblePages(page, totalPages)

  const pendingOnPage = items.filter((item) => item.approval_status === 'pending').length
  const approvedOnPage = items.filter((item) => item.approval_status === 'approved').length
  const rejectedOnPage = items.filter((item) => item.approval_status === 'rejected').length

  const handlePageChange = (nextPage: number) => {
    const safePage = Math.min(Math.max(nextPage, 1), totalPages)
    setPage(safePage)
  }

  const handleGoToPage = () => {
    const nextPage = Number(pageInput)
    if (!Number.isFinite(nextPage)) {
      setPageInput(String(page))
      return
    }
    handlePageChange(Math.trunc(nextPage))
  }

  return (
    <div className="animate-slide-up space-y-6">
      <header className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
        <div>
          <div className="inline-flex items-center gap-2 rounded-full border border-white/[0.08] bg-white/[0.03] px-3 py-1 text-[11px] uppercase tracking-[0.2em] text-zinc-500">
            Ticket dataset
          </div>
          <h1 className="mt-3 text-2xl font-bold tracking-tight text-white">Sample conversations</h1>
          <p className="mt-1.5 max-w-3xl text-sm text-zinc-500">
            Review crawled tickets, filter quickly, approve usable samples, and paginate large datasets without losing context.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2.5">
          <button
            type="button"
            onClick={handleRefresh}
            disabled={loading}
            className="btn-ghost inline-flex items-center gap-2 rounded-xl px-4 py-2.5 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50"
          >
            <RefreshCw size={15} className={loading ? 'animate-spin-slow' : ''} />
            Refresh
          </button>
          <button
            type="button"
            onClick={handleIngestToFile}
            disabled={ingesting}
            className="btn-primary inline-flex items-center gap-2 rounded-xl px-5 py-2.5 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50"
          >
            {ingesting ? <Loader2 size={16} className="animate-spin-slow" /> : <FileDown size={16} />}
            Export approved samples
          </button>
        </div>
      </header>

      <section className="grid gap-3 md:grid-cols-3">
        <SummaryCard label="Total records" value={String(total)} hint={`${rangeStart}-${rangeEnd} visible`} />
        <SummaryCard label="This page" value={String(items.length)} hint={`${pageSize} rows per page`} />
        <SummaryCard
          label="Page approvals"
          value={`${approvedOnPage}/${items.length || 0}`}
          hint={`${pendingOnPage} pending, ${rejectedOnPage} rejected`}
        />
      </section>

      {ingestResult && (
        <div className="animate-fade-in rounded-2xl border border-emerald-500/20 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-300">
          Exported {ingestResult.count} approved sample conversation(s) to {ingestResult.path}
        </div>
      )}

      <section className="glass gradient-border rounded-3xl p-4 md:p-5">
        <div className="flex flex-col gap-4">
          <div className="grid gap-3 xl:grid-cols-[minmax(0,1.6fr)_repeat(3,minmax(0,0.6fr))]">
            <div className="relative">
              <Search size={16} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-zinc-600" />
              <input
                type="text"
                placeholder="Search ID, subject, customer, content..."
                value={filterQ}
                onChange={(e) => setFilterQ(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
                className="input-glass w-full rounded-2xl py-3 pl-10 pr-10 text-sm"
              />
              {filterQ && (
                <button
                  type="button"
                  onClick={() => setFilterQ('')}
                  className="absolute right-2 top-1/2 -translate-y-1/2 rounded-lg p-2 text-zinc-500 transition-colors hover:bg-white/[0.05] hover:text-white"
                  title="Clear search"
                >
                  <X size={14} />
                </button>
              )}
            </div>

            <select
              value={filterStatus}
              onChange={(e) => {
                setFilterStatus(e.target.value)
                setPage(1)
              }}
              aria-label="Filter by status"
              className="input-glass rounded-2xl px-4 py-3 text-sm"
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
              onChange={(e) => {
                setFilterApproval(e.target.value)
                setPage(1)
              }}
              aria-label="Filter by approval"
              className="input-glass rounded-2xl px-4 py-3 text-sm"
            >
              <option value="">All approvals</option>
              <option value="pending">Pending</option>
              <option value="approved">Approved</option>
              <option value="rejected">Rejected</option>
            </select>

            <select
              value={String(pageSize)}
              onChange={(e) => {
                setPageSize(Number(e.target.value))
                setPage(1)
              }}
              aria-label="Rows per page"
              className="input-glass rounded-2xl px-4 py-3 text-sm"
            >
              {PAGE_SIZE_OPTIONS.map((size) => (
                <option key={size} value={size}>
                  {size} rows per page
                </option>
              ))}
            </select>
          </div>

          <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                className="btn-primary inline-flex items-center gap-2 rounded-xl px-4 py-2.5 text-sm font-medium"
                onClick={handleSearch}
              >
                <Filter size={15} />
                Apply filters
              </button>
              <button
                type="button"
                className="btn-ghost inline-flex items-center gap-2 rounded-xl px-4 py-2.5 text-sm font-medium"
                onClick={handleResetFilters}
                disabled={activeFilterCount === 0 && filterQ.length === 0 && pageSize === DEFAULT_PAGE_SIZE}
              >
                <RotateCcw size={15} />
                Reset
              </button>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              {filterQApplied && (
                <FilterChip label={`Query: ${filterQApplied}`} onRemove={() => {
                  setFilterQ('')
                  setFilterQApplied('')
                  setPage(1)
                }} />
              )}
              {filterStatus && (
                <FilterChip label={`Status: ${filterStatus}`} onRemove={() => {
                  setFilterStatus('')
                  setPage(1)
                }} />
              )}
              {filterApproval && (
                <FilterChip label={`Approval: ${filterApproval}`} onRemove={() => {
                  setFilterApproval('')
                  setPage(1)
                }} />
              )}
            </div>
          </div>
        </div>
      </section>

      {error && (
        <div className="animate-fade-in rounded-2xl border border-danger/20 bg-danger/10 px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      )}

      <section className="glass rounded-3xl overflow-hidden">
        <div className="flex flex-col gap-3 border-b border-white/[0.05] px-4 py-4 md:flex-row md:items-center md:justify-between md:px-5">
          <div>
            <div className="text-sm font-medium text-zinc-200">
              Showing {rangeStart}-{rangeEnd} of {total} sample conversation(s)
            </div>
            <div className="mt-1 text-xs text-zinc-500">
              Page {page} of {totalPages} {activeFilterCount > 0 ? `| ${activeFilterCount} active filter(s)` : '| no filters'}
            </div>
          </div>
          <div className="text-xs text-zinc-500">Sorted by latest update first</div>
        </div>

        {loading ? (
          <div className="flex items-center justify-center gap-3 py-24 text-zinc-500">
            <Loader2 size={20} className="animate-spin-slow text-accent" />
            <span className="text-sm">Loading sample conversations...</span>
          </div>
        ) : items.length === 0 ? (
          <div className="flex flex-col items-center px-6 py-20 text-center text-zinc-500">
            <div className="glass-accent glow-sm mb-5 flex h-16 w-16 items-center justify-center rounded-2xl">
              <TicketIcon size={28} className="text-violet-400" />
            </div>
            <p className="mb-1.5 font-semibold text-zinc-300">No sample conversations found</p>
            <p className="max-w-md text-sm">
              Adjust filters or crawl new tickets from the crawl page to populate this dataset.
            </p>
          </div>
        ) : (
          <>
            <div className="hidden overflow-x-auto xl:block">
              <table className="min-w-[1320px] w-full text-sm">
                <thead>
                  <tr className="border-b border-white/[0.04] bg-black/10">
                    <th className="px-5 py-4 text-left text-[11px] font-medium uppercase tracking-[0.16em] text-zinc-500">Ticket</th>
                    <th className="px-5 py-4 text-left text-[11px] font-medium uppercase tracking-[0.16em] text-zinc-500">Customer</th>
                    <th className="px-5 py-4 text-left text-[11px] font-medium uppercase tracking-[0.16em] text-zinc-500">Workflow</th>
                    <th className="px-5 py-4 text-left text-[11px] font-medium uppercase tracking-[0.16em] text-zinc-500">Timeline</th>
                    <th className="px-5 py-4 text-right text-[11px] font-medium uppercase tracking-[0.16em] text-zinc-500">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((ticket) => (
                    <TicketTableRow
                      key={ticket.id}
                      ticket={ticket}
                      onOpen={() => navigate(`/tickets/${ticket.id}`)}
                      onApprove={(status) => handleApproval(ticket, status)}
                    />
                  ))}
                </tbody>
              </table>
            </div>

            <div className="divide-y divide-white/[0.04] xl:hidden">
              {items.map((ticket) => (
                <TicketCard
                  key={ticket.id}
                  ticket={ticket}
                  onOpen={() => navigate(`/tickets/${ticket.id}`)}
                  onApprove={(status) => handleApproval(ticket, status)}
                />
              ))}
            </div>
          </>
        )}
      </section>

      {total > 0 && (
        <section className="glass rounded-3xl px-4 py-4 md:px-5">
          <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
            <div className="text-sm text-zinc-500">
              Page {page} of {totalPages}, displaying rows {rangeStart}-{rangeEnd}.
            </div>

            <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
              <div className="flex items-center gap-1.5">
                <PageButton
                  title="First page"
                  disabled={page <= 1}
                  onClick={() => handlePageChange(1)}
                  icon={<ChevronsLeft size={16} />}
                />
                <PageButton
                  title="Previous page"
                  disabled={page <= 1}
                  onClick={() => handlePageChange(page - 1)}
                  icon={<ChevronLeft size={16} />}
                />
                {visiblePages.map((item, index) =>
                  item === 'ellipsis' ? (
                    <span key={`ellipsis-${index}`} className="px-2 text-sm text-zinc-600">
                      ...
                    </span>
                  ) : (
                    <button
                      key={item}
                      type="button"
                      onClick={() => handlePageChange(item)}
                      className={`h-10 min-w-10 rounded-xl px-3 text-sm font-medium transition-all duration-200 ${
                        item === page
                          ? 'btn-primary'
                          : 'text-zinc-400 hover:bg-white/[0.05] hover:text-white'
                      }`}
                    >
                      {item}
                    </button>
                  )
                )}
                <PageButton
                  title="Next page"
                  disabled={page >= totalPages}
                  onClick={() => handlePageChange(page + 1)}
                  icon={<ChevronRight size={16} />}
                />
                <PageButton
                  title="Last page"
                  disabled={page >= totalPages}
                  onClick={() => handlePageChange(totalPages)}
                  icon={<ChevronsRight size={16} />}
                />
              </div>

              <div className="flex items-center gap-2">
                <span className="text-xs uppercase tracking-[0.14em] text-zinc-600">Go to</span>
                <input
                  type="number"
                  min={1}
                  max={totalPages}
                  value={pageInput}
                  onChange={(e) => setPageInput(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleGoToPage()}
                  className="input-glass w-20 rounded-xl px-3 py-2 text-sm"
                />
                <button
                  type="button"
                  onClick={handleGoToPage}
                  className="btn-ghost rounded-xl px-3 py-2 text-sm font-medium"
                >
                  Go
                </button>
              </div>
            </div>
          </div>
        </section>
      )}
    </div>
  )
}

function TicketTableRow({
  ticket,
  onOpen,
  onApprove,
}: {
  ticket: Ticket
  onOpen: () => void
  onApprove: (status: 'pending' | 'approved' | 'rejected') => void
}) {
  const repliesCount = getRepliesCount(ticket)
  const detailUrl = ticket.detail_url || null

  return (
    <tr
      className="cursor-pointer border-b border-white/[0.03] align-top transition-colors duration-200 last:border-b-0 hover:bg-white/[0.02]"
      onClick={onOpen}
    >
      <td className="px-5 py-5">
        <div className="max-w-[460px] min-w-0">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <code className="rounded-lg bg-violet-500/10 px-2.5 py-1 text-xs font-mono text-violet-400">
              {ticket.external_id || ticket.id.slice(0, 8)}
            </code>
            <span className="rounded-lg border border-white/[0.05] bg-white/[0.03] px-2 py-1 text-[11px] text-zinc-500">
              internal {ticket.id.slice(0, 8)}
            </span>
            <StatusBadge status={ticket.status} />
            <PriorityBadge priority={ticket.priority} />
          </div>

          <div className="text-sm font-medium text-zinc-100">{ticket.subject || '(No subject)'}</div>

          <div className="mt-2 text-sm leading-relaxed text-zinc-400" style={descriptionPreviewStyle}>
            {ticket.description || 'No ticket description available.'}
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-zinc-500">
            <span className="inline-flex items-center gap-1 rounded-lg border border-white/[0.04] bg-black/20 px-2 py-1">
              <MessageSquareText size={12} />
              {repliesCount} repl{repliesCount === 1 ? 'y' : 'ies'}
            </span>
            {ticket.source_file && (
              <span className="rounded-lg border border-white/[0.04] bg-black/20 px-2 py-1">
                {ticket.source_file}
              </span>
            )}
          </div>
        </div>
      </td>

      <td className="px-5 py-5">
        <div className="max-w-[260px] space-y-2">
          <div className="flex items-start gap-2 text-sm text-zinc-300">
            <UserRound size={14} className="mt-0.5 shrink-0 text-zinc-600" />
            <div>
              <div>{ticket.name || '-'}</div>
              {ticket.client_id && <div className="text-xs text-zinc-500">Client ID: {ticket.client_id}</div>}
            </div>
          </div>
          <div className="flex items-start gap-2 text-sm text-zinc-400">
            <Mail size={14} className="mt-0.5 shrink-0 text-zinc-600" />
            {ticket.email ? (
              <a
                href={`mailto:${ticket.email}`}
                className="truncate text-violet-400 transition-colors hover:text-violet-300"
                onClick={(e) => e.stopPropagation()}
              >
                {ticket.email}
              </a>
            ) : (
              <span>-</span>
            )}
          </div>
        </div>
      </td>

      <td className="px-5 py-5">
        <div className="space-y-3">
          <ApprovalBadge status={ticket.approval_status} />
          <ApprovalActions current={ticket.approval_status} onApprove={onApprove} />
        </div>
      </td>

      <td className="px-5 py-5">
        <div className="space-y-2 text-sm text-zinc-400">
          <div>
            <div className="text-xs uppercase tracking-[0.14em] text-zinc-600">Created</div>
            <div>{formatDateTime(ticket.created_at)}</div>
          </div>
          <div>
            <div className="text-xs uppercase tracking-[0.14em] text-zinc-600">Updated</div>
            <div>{formatDateTime(ticket.updated_at)}</div>
          </div>
        </div>
      </td>

      <td className="px-5 py-5">
        <div className="flex items-center justify-end gap-2">
          {detailUrl && (
            <a
              href={detailUrl}
              target="_blank"
              rel="noopener noreferrer"
              onClick={(e) => e.stopPropagation()}
              className="inline-flex items-center gap-1 rounded-xl border border-white/[0.06] bg-white/[0.03] px-3 py-2 text-sm text-zinc-300 transition-colors hover:bg-white/[0.06] hover:text-white"
            >
              WHMCS
              <ArrowUpRight size={14} />
            </a>
          )}
          <Link
            to={`/tickets/${ticket.id}`}
            onClick={(e) => e.stopPropagation()}
            className="inline-flex items-center gap-1 rounded-xl border border-violet-500/15 bg-violet-500/10 px-3 py-2 text-sm text-violet-300 transition-colors hover:bg-violet-500/15 hover:text-violet-200"
          >
            Details
            <ExternalLink size={14} />
          </Link>
        </div>
      </td>
    </tr>
  )
}

function TicketCard({
  ticket,
  onOpen,
  onApprove,
}: {
  ticket: Ticket
  onOpen: () => void
  onApprove: (status: 'pending' | 'approved' | 'rejected') => void
}) {
  const repliesCount = getRepliesCount(ticket)
  const detailUrl = ticket.detail_url || null

  return (
    <article className="space-y-4 px-4 py-5 md:px-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <code className="rounded-lg bg-violet-500/10 px-2.5 py-1 text-xs font-mono text-violet-400">
              {ticket.external_id || ticket.id.slice(0, 8)}
            </code>
            <StatusBadge status={ticket.status} />
            <PriorityBadge priority={ticket.priority} />
          </div>
          <h2 className="text-base font-semibold text-white">{ticket.subject || '(No subject)'}</h2>
        </div>
        <ApprovalBadge status={ticket.approval_status} />
      </div>

      <p className="text-sm leading-relaxed text-zinc-400" style={descriptionPreviewStyle}>
        {ticket.description || 'No ticket description available.'}
      </p>

      <div className="grid gap-3 sm:grid-cols-2">
        <InfoBlock
          label="Customer"
          value={ticket.name || '-'}
          extra={ticket.email || ticket.client_id || 'No customer contact'}
        />
        <InfoBlock
          label="Timeline"
          value={`Updated ${formatDateTime(ticket.updated_at)}`}
          extra={`Created ${formatDateTime(ticket.created_at)}`}
        />
        <InfoBlock
          label="Replies"
          value={`${repliesCount} repl${repliesCount === 1 ? 'y' : 'ies'}`}
          extra={ticket.source_file || 'No source file'}
        />
        <div>
          <div className="mb-1 text-[11px] uppercase tracking-[0.16em] text-zinc-600">Approval</div>
          <ApprovalActions current={ticket.approval_status} onApprove={onApprove} />
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={onOpen}
          className="btn-primary inline-flex items-center gap-2 rounded-xl px-4 py-2.5 text-sm font-medium"
        >
          Open details
          <ExternalLink size={14} />
        </button>
        {detailUrl && (
          <a
            href={detailUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="btn-ghost inline-flex items-center gap-2 rounded-xl px-4 py-2.5 text-sm font-medium"
          >
            Open WHMCS
            <ArrowUpRight size={14} />
          </a>
        )}
      </div>
    </article>
  )
}

function SummaryCard({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <div className="glass-light rounded-2xl px-4 py-4">
      <div className="text-xs uppercase tracking-[0.16em] text-zinc-600">{label}</div>
      <div className="mt-2 text-2xl font-semibold text-white">{value}</div>
      <div className="mt-1 text-sm text-zinc-500">{hint}</div>
    </div>
  )
}

function FilterChip({ label, onRemove }: { label: string; onRemove: () => void }) {
  return (
    <button
      type="button"
      onClick={onRemove}
      className="inline-flex items-center gap-1.5 rounded-full border border-white/[0.06] bg-white/[0.03] px-3 py-1.5 text-xs text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-white"
    >
      {label}
      <X size={12} />
    </button>
  )
}

function InfoBlock({ label, value, extra }: { label: string; value: string; extra: string }) {
  return (
    <div>
      <div className="mb-1 text-[11px] uppercase tracking-[0.16em] text-zinc-600">{label}</div>
      <div className="text-sm font-medium text-zinc-200">{value}</div>
      <div className="mt-1 text-sm text-zinc-500">{extra}</div>
    </div>
  )
}

function PageButton({
  title,
  disabled,
  onClick,
  icon,
}: {
  title: string
  disabled: boolean
  onClick: () => void
  icon: ReactNode
}) {
  return (
    <button
      type="button"
      title={title}
      disabled={disabled}
      onClick={onClick}
      className="flex h-10 w-10 items-center justify-center rounded-xl text-zinc-400 transition-colors hover:bg-white/[0.05] hover:text-white disabled:cursor-not-allowed disabled:opacity-30"
    >
      {icon}
    </button>
  )
}

function ApprovalActions({
  current,
  onApprove,
}: {
  current: string
  onApprove: (status: 'pending' | 'approved' | 'rejected') => void
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      <ApprovalActionButton
        title="Approve"
        active={current === 'approved'}
        icon={<CheckCircle2 size={14} />}
        className="text-emerald-400 hover:bg-emerald-500/15"
        onClick={() => onApprove('approved')}
      />
      <ApprovalActionButton
        title="Set pending"
        active={current === 'pending'}
        icon={<Clock3 size={14} />}
        className="text-amber-400 hover:bg-amber-500/15"
        onClick={() => onApprove('pending')}
      />
      <ApprovalActionButton
        title="Reject"
        active={current === 'rejected'}
        icon={<XCircle size={14} />}
        className="text-red-400 hover:bg-red-500/15"
        onClick={() => onApprove('rejected')}
      />
    </div>
  )
}

function ApprovalActionButton({
  title,
  active,
  icon,
  className,
  onClick,
}: {
  title: string
  active: boolean
  icon: ReactNode
  className: string
  onClick: () => void
}) {
  return (
    <button
      type="button"
      title={title}
      onClick={(e) => {
        e.stopPropagation()
        onClick()
      }}
      className={`inline-flex items-center gap-1.5 rounded-xl border px-3 py-2 text-sm transition-colors ${
        active ? 'border-white/[0.08] bg-white/[0.05]' : 'border-white/[0.05] bg-black/20'
      } ${className}`}
    >
      {icon}
      {title}
    </button>
  )
}

function ApprovalBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    pending: 'border-amber-500/15 bg-amber-500/10 text-amber-400',
    approved: 'border-emerald-500/15 bg-emerald-500/10 text-emerald-400',
    rejected: 'border-red-500/15 bg-red-500/10 text-red-400',
  }
  const labels: Record<string, string> = {
    pending: 'Pending',
    approved: 'Approved',
    rejected: 'Rejected',
  }
  const cls = styles[status] || 'border-white/[0.06] bg-white/[0.03] text-zinc-400'

  return (
    <span className={`inline-flex rounded-xl border px-3 py-1.5 text-xs font-medium ${cls}`}>
      {labels[status] || status}
    </span>
  )
}

function StatusBadge({ status }: { status: string }) {
  const normalized = (status || '').toLowerCase()
  const styles: Record<string, string> = {
    open: 'border-amber-500/15 bg-amber-500/10 text-amber-400',
    answered: 'border-blue-500/15 bg-blue-500/10 text-blue-400',
    'customer-reply': 'border-emerald-500/15 bg-emerald-500/10 text-emerald-400',
    closed: 'border-zinc-500/15 bg-zinc-500/10 text-zinc-400',
    'in progress': 'border-cyan-500/15 bg-cyan-500/10 text-cyan-400',
  }
  const cls = styles[normalized] || 'border-white/[0.06] bg-white/[0.03] text-zinc-400'

  return (
    <span className={`inline-flex rounded-lg border px-2.5 py-1 text-xs font-medium ${cls}`}>
      {status || 'N/A'}
    </span>
  )
}

function PriorityBadge({ priority }: { priority: string | null }) {
  if (!priority) {
    return (
      <span className="inline-flex rounded-lg border border-white/[0.06] bg-white/[0.03] px-2.5 py-1 text-xs font-medium text-zinc-500">
        No priority
      </span>
    )
  }

  const normalized = priority.toLowerCase()
  const styles: Record<string, string> = {
    low: 'border-zinc-500/15 bg-zinc-500/10 text-zinc-400',
    medium: 'border-blue-500/15 bg-blue-500/10 text-blue-400',
    high: 'border-amber-500/15 bg-amber-500/10 text-amber-400',
    urgent: 'border-red-500/15 bg-red-500/10 text-red-400',
  }
  const cls = styles[normalized] || 'border-white/[0.06] bg-white/[0.03] text-zinc-400'

  return (
    <span className={`inline-flex rounded-lg border px-2.5 py-1 text-xs font-medium ${cls}`}>
      {priority}
    </span>
  )
}

function buildVisiblePages(currentPage: number, totalPages: number): Array<number | 'ellipsis'> {
  if (totalPages <= 7) {
    return Array.from({ length: totalPages }, (_, index) => index + 1)
  }

  const pages = new Set<number>([1, 2, totalPages - 1, totalPages, currentPage - 1, currentPage, currentPage + 1])
  const sortedPages = Array.from(pages)
    .filter((page) => page >= 1 && page <= totalPages)
    .sort((a, b) => a - b)
  const visible: Array<number | 'ellipsis'> = []

  sortedPages.forEach((page, index) => {
    const previous = sortedPages[index - 1]
    if (previous && page - previous > 1) {
      visible.push('ellipsis')
    }
    visible.push(page)
  })

  return visible
}

function getRepliesCount(ticket: Ticket) {
  if (!ticket.metadata || typeof ticket.metadata !== 'object') return 0
  const replies = ticket.metadata.replies
  return Array.isArray(replies) ? replies.length : 0
}

function formatDateTime(value: string | null) {
  if (!value) return '-'
  return new Date(value).toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}
