import { useState, useEffect } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { tickets, conversations, type TicketDetail } from '../api/client'
import {
  ArrowLeft,
  Loader2,
  ExternalLink,
  User,
  Mail,
  Calendar,
  Tag,
  MessageSquare,
  MessageCirclePlus,
} from 'lucide-react'

interface Reply {
  role?: string
  name?: string
  content?: string
  posted?: string
}

export default function TicketDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [ticket, setTicket] = useState<TicketDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [startingConv, setStartingConv] = useState(false)

  const load = async () => {
    if (!id) return
    setLoading(true)
    setError(null)
    try {
      const data = await tickets.get(id)
      setTicket(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load conversation')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [id])

  const handleStartConversation = async () => {
    if (!ticket) return
    setStartingConv(true)
    try {
      const sourceId = ticket.id
      const conv = await conversations.create('ticket', sourceId)
      navigate(`/conversations/${conv.id}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to create conversation')
    } finally {
      setStartingConv(false)
    }
  }

  if (!id) return null

  if (loading) {
    return (
      <div className="flex items-center justify-center gap-3 py-24 animate-fade-in">
        <Loader2 size={22} className="animate-spin-slow text-accent" />
        <span className="text-zinc-500">Loading conversation...</span>
      </div>
    )
  }

  if (error && !ticket) {
    return (
      <div className="animate-fade-in">
        <div className="p-3.5 rounded-xl mb-5 bg-danger/10 border border-danger/20 text-red-300 text-sm">
          {error}
        </div>
        <Link
          to="/tickets"
          className="inline-flex items-center gap-2 text-sm text-zinc-500 hover:text-white transition-colors"
        >
          <ArrowLeft size={16} /> Back to sample conversations
        </Link>
      </div>
    )
  }

  if (!ticket) return null

  const replies: Reply[] =
    ticket.metadata && typeof ticket.metadata === 'object' && Array.isArray(ticket.metadata.replies)
      ? (ticket.metadata.replies as Reply[])
      : []

  return (
    <div className="animate-slide-up">
      <header className="flex items-center gap-3 pb-5 mb-6" style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
        <Link
          to="/tickets"
          className="p-2 rounded-xl text-zinc-500 hover:text-white hover:bg-white/[0.05] transition-colors"
        >
          <ArrowLeft size={18} />
        </Link>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2.5 flex-wrap">
            <h1 className="text-lg font-semibold text-white truncate">{ticket.subject || '(No subject)'}</h1>
            <code className="text-xs text-violet-400 bg-violet-500/10 px-2 py-1 rounded-lg font-mono">
              {ticket.external_id || ticket.id.slice(0, 8)}
            </code>
            {ticket.detail_url && (
              <a
                href={ticket.detail_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-xs text-violet-400 hover:text-violet-300 transition-colors"
              >
                <ExternalLink size={13} /> Open in WHMCS
              </a>
            )}
            <button
              onClick={handleStartConversation}
              disabled={startingConv}
              className="btn-primary inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-xl text-xs font-medium disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {startingConv ? <Loader2 size={13} className="animate-spin" /> : <MessageCirclePlus size={13} />}
              Start conversation
            </button>
          </div>
          <div className="flex items-center gap-3 text-xs text-zinc-500 mt-1.5 flex-wrap">
            <span className="inline-flex items-center gap-1">
              <Tag size={12} />
              {ticket.status || 'N/A'}
            </span>
            {ticket.priority && (
              <>
                <span className="text-zinc-700">·</span>
                <span>Priority: {ticket.priority}</span>
              </>
            )}
            {ticket.updated_at && (
              <>
                <span className="text-zinc-700">·</span>
                <span>
                  {new Date(ticket.updated_at).toLocaleDateString('en-US', {
                    month: 'short',
                    day: 'numeric',
                    year: 'numeric',
                    hour: '2-digit',
                    minute: '2-digit',
                  })}
                </span>
              </>
            )}
          </div>
        </div>
      </header>

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2 space-y-6">
          {ticket.description && (
            <section className="glass rounded-2xl p-5">
              <h2 className="text-sm font-medium text-zinc-500 mb-3">Description / Main content</h2>
              <div className="text-sm text-zinc-300 whitespace-pre-wrap leading-relaxed">{ticket.description}</div>
            </section>
          )}

          {replies.length > 0 && (
            <section className="glass rounded-2xl overflow-hidden">
              <h2 className="text-sm font-medium text-zinc-500 px-5 py-4 border-b border-white/[0.04] flex items-center gap-2">
                <MessageSquare size={15} />
                Conversation ({replies.length})
              </h2>
              <div className="divide-y divide-white/[0.04]">
                {replies.map((r, i) => (
                  <div key={i} className="p-5">
                    <div className="flex items-center gap-2.5 mb-2.5">
                      <span
                        className={`text-xs font-medium px-2.5 py-1 rounded-lg border ${
                          r.role === 'staff' || r.role === 'owner'
                            ? 'bg-violet-500/10 text-violet-400 border-violet-500/15'
                            : 'bg-white/[0.03] text-zinc-400 border-white/[0.06]'
                        }`}
                      >
                        {r.role || 'client'}
                      </span>
                      {r.name && <span className="text-sm text-zinc-500">{r.name}</span>}
                      {r.posted && (
                        <span className="text-xs text-zinc-600 ml-auto">
                          {new Date(r.posted).toLocaleString('en-US')}
                        </span>
                      )}
                    </div>
                    <div className="text-sm text-zinc-300 whitespace-pre-wrap leading-relaxed">{r.content || ''}</div>
                  </div>
                ))}
              </div>
            </section>
          )}
        </div>

        <div className="space-y-5">
          <section className="glass rounded-2xl p-5">
            <h2 className="text-sm font-medium text-zinc-500 mb-3">Customer info</h2>
            <div className="space-y-2.5 text-sm">
              {ticket.name && (
                <div className="flex items-center gap-2.5 text-zinc-300">
                  <User size={14} className="text-zinc-600 shrink-0" />
                  {ticket.name}
                </div>
              )}
              {ticket.email && (
                <div className="flex items-center gap-2.5 text-zinc-300">
                  <Mail size={14} className="text-zinc-600 shrink-0" />
                  <a href={`mailto:${ticket.email}`} className="text-violet-400 hover:text-violet-300 truncate transition-colors">
                    {ticket.email}
                  </a>
                </div>
              )}
              {ticket.client_id && (
                <div className="flex items-center gap-2 text-zinc-500 text-xs">
                  Client ID: {ticket.client_id}
                </div>
              )}
              {!ticket.name && !ticket.email && (
                <p className="text-zinc-500 text-sm">No information</p>
              )}
            </div>
          </section>

          <section className="glass rounded-2xl p-5">
            <h2 className="text-sm font-medium text-zinc-500 mb-3">Metadata</h2>
            <div className="text-xs text-zinc-500 space-y-1.5">
              {ticket.source_file && <p>Source: {ticket.source_file}</p>}
              {ticket.created_at && (
                <p className="flex items-center gap-1.5">
                  <Calendar size={12} />
                  Created: {new Date(ticket.created_at).toLocaleString('en-US')}
                </p>
              )}
            </div>
          </section>
        </div>
      </div>
    </div>
  )
}
