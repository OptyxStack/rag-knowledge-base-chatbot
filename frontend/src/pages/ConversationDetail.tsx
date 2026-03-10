import { useState, useEffect, useRef } from 'react'
import { useParams, Link } from 'react-router-dom'
import { conversations, type ConversationDetail as ConvDetail, type Message, type FlowDebug } from '../api/client'
import {
  ArrowLeft,
  Copy,
  Check,
  Send,
  Loader2,
  Bot,
  User,
  ChevronDown,
  ChevronRight,
  Zap,
  Search,
  Database,
  FileText,
  Brain,
  AlertTriangle,
  ExternalLink,
  Sparkles,
} from 'lucide-react'

export default function ConversationDetail() {
  const { id } = useParams<{ id: string }>()
  const [conv, setConv] = useState<ConvDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [streamingContent, setStreamingContent] = useState('')
  const [useStreaming, setUseStreaming] = useState(true)
  const messagesEndRef = useRef<HTMLDivElement>(null)

  const load = async () => {
    if (!id) return
    setLoading(true)
    setError(null)
    try {
      const data = await conversations.get(id)
      setConv(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load conversation')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [id])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [conv?.messages])

  const handleSend = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!id || !input.trim() || sending) return
    const content = input.trim()
    setInput('')
    setSending(true)
    setError(null)
    setStreamingContent('')

    try {
      if (useStreaming) {
        const res = await conversations.sendMessageStream(id, content)
        if (!res.ok) throw new Error(res.statusText)
        const reader = res.body?.getReader()
        const decoder = new TextDecoder()
        let buffer = ''
        if (reader) {
          while (true) {
            const { done, value } = await reader.read()
            if (done) break
            buffer += decoder.decode(value, { stream: true })
            const events = buffer.split('\n\n')
            buffer = events.pop() || ''
            for (const ev of events) {
              const m = ev.match(/^data:\s*(.+)$/m)
              if (m) {
                try {
                  const data = JSON.parse(m[1])
                  if (data.type === 'content') setStreamingContent((prev) => prev + (data.data || ''))
                  else if (data.type === 'done') break
                } catch {}
              }
            }
          }
        }
        await load()
      } else {
        await conversations.sendMessage(id, content)
        await load()
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to send message')
      setInput(content)
    } finally {
      setSending(false)
      setStreamingContent('')
    }
  }

  if (!id) return null

  if (loading) return (
    <div className="flex items-center justify-center gap-3 py-24 animate-fade-in">
      <Loader2 size={22} className="animate-spin-slow text-accent" />
      <span className="text-zinc-500">Loading conversation...</span>
    </div>
  )

  if (error && !conv) {
    return (
      <div className="animate-fade-in">
        <div className="p-3.5 rounded-xl mb-5 bg-danger/10 border border-danger/20 text-red-300 text-sm">{error}</div>
        <Link to="/" className="inline-flex items-center gap-2 text-sm text-zinc-500 hover:text-white transition-colors">
          <ArrowLeft size={16} /> Back to conversations
        </Link>
      </div>
    )
  }

  if (!conv) return null

  return (
    <div className="flex flex-col h-[calc(100vh-3rem)] lg:h-[calc(100vh-2rem)] animate-slide-up">
      <header className="flex items-center gap-3 pb-4 mb-0 shrink-0" style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
        <Link
          to="/"
          className="p-2 rounded-xl text-zinc-500 hover:text-white hover:bg-white/[0.05] transition-colors"
        >
          <ArrowLeft size={18} />
        </Link>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <h1 className="text-lg font-semibold text-white truncate">Conversation</h1>
            <CopyableId id={conv.id} />
          </div>
          <div className="flex items-center gap-3 text-xs text-zinc-500 mt-0.5">
            <span className="capitalize">{conv.source_type} / {conv.source_id}</span>
            <span className="text-zinc-700">·</span>
            <span>{new Date(conv.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}</span>
            <span className="text-zinc-700">·</span>
            <span>{conv.messages.length} messages</span>
          </div>
        </div>
      </header>

      {error && (
        <div className="p-3.5 rounded-xl my-3 bg-danger/10 border border-danger/20 text-red-300 text-sm animate-fade-in">
          {error}
        </div>
      )}

      <div className="flex-1 overflow-y-auto py-5 space-y-4">
        {conv.messages.length === 0 && (
          <div className="flex flex-col items-center justify-center py-20 text-zinc-500">
            <div className="w-14 h-14 rounded-2xl glass-accent flex items-center justify-center mb-4 glow-sm">
              <Sparkles size={24} className="text-violet-400" />
            </div>
            <p className="text-sm">Send a message to start the conversation</p>
          </div>
        )}
        {conv.messages.map((m) => (
          <MessageBubble key={m.id} message={m} />
        ))}
        {streamingContent && (
          <div className="flex items-start gap-3 animate-fade-in">
            <div
              className="w-9 h-9 rounded-xl flex items-center justify-center shrink-0 mt-0.5"
              style={{ background: 'linear-gradient(135deg, rgba(124,58,237,0.2), rgba(59,130,246,0.15))' }}
            >
              <Bot size={16} className="text-violet-400" />
            </div>
            <div className="glass rounded-2xl rounded-tl-lg px-4 py-3.5 max-w-[85%]">
              <div className="text-zinc-200 whitespace-pre-wrap text-sm leading-relaxed">{streamingContent}</div>
              <span className="inline-block w-2 h-4 ml-0.5 bg-violet-400 animate-pulse" />
            </div>
          </div>
        )}
        {sending && !streamingContent && (
          <div className="flex items-start gap-3 animate-fade-in">
            <div
              className="w-9 h-9 rounded-xl flex items-center justify-center shrink-0 mt-0.5"
              style={{ background: 'linear-gradient(135deg, rgba(124,58,237,0.2), rgba(59,130,246,0.15))' }}
            >
              <Bot size={16} className="text-violet-400" />
            </div>
            <div className="glass rounded-2xl rounded-tl-lg px-4 py-3.5">
              <div className="flex items-center gap-2.5 text-zinc-500 text-sm">
                <div className="flex gap-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-violet-400 animate-pulse-soft" style={{ animationDelay: '0ms' }} />
                  <span className="w-1.5 h-1.5 rounded-full bg-violet-400 animate-pulse-soft" style={{ animationDelay: '200ms' }} />
                  <span className="w-1.5 h-1.5 rounded-full bg-violet-400 animate-pulse-soft" style={{ animationDelay: '400ms' }} />
                </div>
                Thinking...
              </div>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      <form
        className="flex flex-col gap-2 pt-4 shrink-0"
        style={{ borderTop: '1px solid rgba(255,255,255,0.04)' }}
        onSubmit={handleSend}
      >
        <div className="flex items-end gap-3">
        <div className="flex-1 relative">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Type a message..."
            disabled={sending}
            maxLength={10000}
            className="w-full px-5 py-3.5 rounded-2xl input-glass text-sm disabled:opacity-50 pr-4"
          />
        </div>
        <button
          type="submit"
          className="btn-primary p-3.5 rounded-2xl disabled:opacity-30 disabled:cursor-not-allowed shrink-0"
          disabled={sending || !input.trim()}
        >
          {sending ? <Loader2 size={18} className="animate-spin-slow" /> : <Send size={18} />}
        </button>
        </div>
        <label className="flex items-center gap-2 text-xs text-zinc-500 cursor-pointer w-fit">
          <input
            type="checkbox"
            checked={useStreaming}
            onChange={(e) => setUseStreaming(e.target.checked)}
            className="rounded border-white/10"
          />
          Stream response
        </label>
      </form>
    </div>
  )
}

function CopyableId({ id }: { id: string }) {
  const [copied, setCopied] = useState(false)
  const copy = () => {
    navigator.clipboard.writeText(id)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }
  return (
    <button
      onClick={copy}
      className="inline-flex items-center gap-1.5 px-2 py-1 rounded-lg text-xs font-mono text-zinc-500
                 bg-white/[0.03] hover:bg-white/[0.06] hover:text-violet-400 transition-colors"
      title="Copy ID"
    >
      {id.slice(0, 8)}...
      {copied ? <Check size={11} className="text-emerald-400" /> : <Copy size={11} />}
    </button>
  )
}

function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === 'user'
  const [showFlow, setShowFlow] = useState(false)
  const debug = message.debug
  const hasDebug = debug && (
    debug.decision != null || debug.confidence != null || debug.trace_id ||
    debug.source_lang || debug.evidence_eval || debug.self_critic_regenerated || debug.final_polish_applied ||
    (debug.stage_reasons && debug.stage_reasons.length > 0) || debug.termination_reason
  )

  return (
    <div className={`flex items-start gap-3 ${isUser ? 'flex-row-reverse' : ''}`}>
      <div
        className="w-9 h-9 rounded-xl flex items-center justify-center shrink-0 mt-0.5"
        style={isUser
          ? { background: 'linear-gradient(135deg, #7c3aed, #6d28d9)' }
          : { background: 'linear-gradient(135deg, rgba(124,58,237,0.15), rgba(59,130,246,0.1))' }
        }
      >
        {isUser ? <User size={15} className="text-white" /> : <Bot size={15} className="text-violet-400" />}
      </div>

      <div className={`max-w-[80%] min-w-0 ${isUser ? 'items-end' : 'items-start'} flex flex-col`}>
        <div
          className={`px-4 py-3 text-sm leading-relaxed
            ${isUser
              ? 'rounded-2xl rounded-tr-lg text-white'
              : 'glass rounded-2xl rounded-tl-lg'
            }`}
          style={isUser
            ? { background: 'linear-gradient(135deg, #7c3aed, #6d28d9)' }
            : undefined
          }
        >
          <div className="whitespace-pre-wrap break-words">{message.content}</div>
        </div>

        <div className={`flex items-center gap-2 mt-1.5 px-1 ${isUser ? 'flex-row-reverse' : ''}`}>
          <span className="text-[11px] text-zinc-600">
            {new Date(message.created_at).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })}
          </span>
        </div>

        {message.citations && message.citations.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mt-2">
            {message.citations.map((c, i) => (
              <a
                key={i}
                href={c.source_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-lg
                           bg-violet-500/10 text-violet-400 border border-violet-500/15
                           hover:bg-violet-500/15 transition-colors"
              >
                <ExternalLink size={10} />
                {c.doc_type || c.source_url || c.chunk_id}
              </a>
            ))}
          </div>
        )}

        {!isUser && hasDebug && (
          <div className="mt-2.5 w-full">
            <div className="flex flex-wrap items-center gap-1.5 mb-1.5">
              {debug.decision != null && (
                <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-medium bg-violet-500/10 text-violet-400 border border-violet-500/15">
                  <Zap size={10} />
                  {debug.decision}
                </span>
              )}
              {debug.confidence != null && (
                <ConfidenceBadge value={debug.confidence} />
              )}
              {debug.intent_cache && (
                <span className="px-2.5 py-1 rounded-lg text-[11px] bg-white/[0.03] text-zinc-400 border border-white/[0.05]">
                  cache: {debug.intent_cache}
                </span>
              )}
            </div>
            <button
              type="button"
              className="inline-flex items-center gap-1.5 text-[11px] text-zinc-600 hover:text-zinc-400 transition-colors py-1"
              onClick={() => setShowFlow((v) => !v)}
            >
              {showFlow ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
              Debug details
            </button>
            {showFlow && <FlowDebugPanel debug={debug} />}
          </div>
        )}
      </div>
    </div>
  )
}

function ConfidenceBadge({ value }: { value: number }) {
  const pct = Math.round(value * 100)
  const color = pct >= 80
    ? 'text-emerald-400 bg-emerald-500/10 border-emerald-500/15'
    : pct >= 50
      ? 'text-amber-400 bg-amber-500/10 border-amber-500/15'
      : 'text-red-400 bg-red-500/10 border-red-500/15'
  return (
    <span className={`px-2.5 py-1 rounded-lg text-[11px] font-medium border ${color}`}>
      {pct}%
    </span>
  )
}

function FlowDebugPanel({ debug }: { debug: FlowDebug }) {
  return (
    <div className="mt-2 glass rounded-2xl overflow-hidden text-xs animate-slide-up">
      {(debug.stage_reasons && debug.stage_reasons.length > 0) || debug.termination_reason ? (
        <DebugSection icon={<Zap size={13} />} title="Decision Path">
          <div className="space-y-1.5 text-zinc-400">
            {debug.termination_reason && (
              <div>Ended: <span className="text-zinc-300 capitalize">{debug.termination_reason.replace(/_/g, ' ')}</span></div>
            )}
            {debug.stage_reasons && debug.stage_reasons.length > 0 && (
              <div>
                <div className="mb-1 text-zinc-500">Pipeline timeline:</div>
                <ol className="list-decimal pl-4 space-y-0.5">
                  {debug.stage_reasons.map((s, i) => (
                    <li key={i} className="text-zinc-300 font-mono text-[11px]">{s}</li>
                  ))}
                </ol>
              </div>
            )}
          </div>
        </DebugSection>
      ) : null}

      {(debug.decision != null || debug.confidence != null || debug.followup_questions?.length || debug.decision_router?.reason_human) && (
        <DebugSection icon={<Zap size={13} />} title="Decision & Confidence">
          <div className="space-y-1.5 text-zinc-400">
            {debug.decision != null && <div>Decision: <span className="text-zinc-300">{debug.decision}</span></div>}
            {debug.decision_router?.reason_human && (
              <div>Reason: <span className="text-zinc-300">{debug.decision_router.reason_human}</span></div>
            )}
            {debug.confidence != null && <div>Confidence: <span className="text-zinc-300">{(debug.confidence * 100).toFixed(1)}%</span></div>}
            {debug.followup_questions && debug.followup_questions.length > 0 && (
              <div>
                <div className="mb-1">Follow-up questions:</div>
                <ul className="list-disc pl-4 space-y-0.5">
                  {debug.followup_questions.map((q, i) => <li key={i} className="text-zinc-300">{q}</li>)}
                </ul>
              </div>
            )}
          </div>
        </DebugSection>
      )}

      <DebugSection icon={<Brain size={13} />} title="Trace & Model">
        <div className="space-y-1 text-zinc-400">
          {debug.trace_id && <div>Trace: <span className="font-mono text-zinc-500">{debug.trace_id}</span></div>}
          {debug.model_used && <div>Model: <span className="text-zinc-300">{debug.model_used}</span></div>}
          {debug.attempt != null && <div>Attempt: <span className="text-zinc-300">{debug.attempt}</span></div>}
          {debug.intent_cache && <div>Intent cache: <span className="text-zinc-300">{debug.intent_cache}</span></div>}
          {debug.query_spec?.extraction_mode && (
            <div>Query extraction: <span className="text-zinc-300">{debug.query_spec.extraction_mode}</span></div>
          )}
        </div>
      </DebugSection>

      {debug.query_rewrite && (
        <DebugSection icon={<Search size={13} />} title="Query Rewrite">
          <div className="font-mono bg-black/20 p-3 rounded-xl space-y-1.5 text-zinc-400 text-[11px]">
            <div>Keyword: <span className="text-zinc-300">{debug.query_rewrite.keyword_query}</span></div>
            <div>Semantic: <span className="text-zinc-300">{debug.query_rewrite.semantic_query}</span></div>
          </div>
        </DebugSection>
      )}

      {debug.retrieval_stats && (
        <DebugSection icon={<Database size={13} />} title="Retrieval">
          <div className="space-y-2">
            <div className="flex flex-wrap gap-2">
              <StatPill label="BM25" value={debug.retrieval_stats.bm25_count} />
              <StatPill label="Vector" value={debug.retrieval_stats.vector_count} />
              <StatPill label="Merged" value={debug.retrieval_stats.merged_count} />
              <StatPill label="Reranked" value={debug.retrieval_stats.reranked_count} />
            </div>
            {debug.quality_report?.hard_requirement_coverage && Object.keys(debug.quality_report.hard_requirement_coverage).length > 0 && (
              <div>
                <div className="mb-1 text-zinc-500">Hard requirement coverage:</div>
                <div className="flex flex-wrap gap-2">
                  {Object.entries(debug.quality_report.hard_requirement_coverage).map(([req, covered]) => (
                    <span
                      key={req}
                      className={`px-2 py-0.5 rounded text-[10px] ${covered ? 'bg-emerald-500/20 text-emerald-400' : 'bg-amber-500/20 text-amber-400'}`}
                    >
                      {req}: {covered ? '✓' : '✗'}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        </DebugSection>
      )}

      {debug.evidence_summary && debug.evidence_summary.length > 0 && (
        <DebugSection icon={<FileText size={13} />} title={`Evidence (${debug.evidence_summary.length} chunks)`}>
          <div className="space-y-2">
            {debug.evidence_summary.map((e, i) => (
              <div key={i} className="p-3 bg-black/20 rounded-xl">
                <div className="flex items-center gap-2 mb-1.5">
                  <a href={e.source_url} target="_blank" rel="noopener noreferrer" className="text-violet-400 hover:text-violet-300 text-xs">
                    {e.doc_type} · {e.chunk_id.slice(0, 8)}
                  </a>
                  {e.score != null && (
                    <span className="text-zinc-600 text-[10px] ml-auto">score: {e.score.toFixed(3)}</span>
                  )}
                </div>
                <div className="text-zinc-500 whitespace-pre-wrap break-words text-[11px] max-h-32 overflow-y-auto leading-relaxed">
                  {e.snippet}
                </div>
              </div>
            ))}
          </div>
        </DebugSection>
      )}

      {debug.prompt_preview && (
        <DebugSection icon={<FileText size={13} />} title="Prompt Preview">
          <div className="space-y-2.5">
            <div>
              <div className="text-zinc-600 mb-1">System ({debug.prompt_preview.system_length} chars)</div>
              <pre className="font-mono text-[11px] whitespace-pre-wrap break-words bg-black/20 p-3 rounded-xl text-zinc-500 max-h-96 overflow-y-auto">
                {debug.prompt_preview.system_preview}
              </pre>
            </div>
            <div>
              <div className="text-zinc-600 mb-1">User ({debug.prompt_preview.user_length} chars)</div>
              <pre className="font-mono text-[11px] whitespace-pre-wrap break-words bg-black/20 p-3 rounded-xl text-zinc-500 max-h-96 overflow-y-auto">
                {debug.prompt_preview.user_preview}
              </pre>
            </div>
          </div>
        </DebugSection>
      )}

      {(debug.llm_tokens || debug.cost_usd != null) && (
        <DebugSection icon={<Brain size={13} />} title="LLM Usage & Cost">
          <div className="flex flex-wrap gap-3 items-center">
            {debug.llm_tokens && (
              <>
                <StatPill label="Input" value={debug.llm_tokens.input} />
                <StatPill label="Output" value={debug.llm_tokens.output} />
              </>
            )}
            {debug.cost_usd != null && debug.cost_usd > 0 && (
              <StatPill label="Cost" value={`$${debug.cost_usd.toFixed(6)}`} />
            )}
          </div>
          {debug.llm_usage_breakdown && debug.llm_usage_breakdown.length > 0 && (
            <div className="mt-2 space-y-1 text-[11px] text-zinc-500">
              {debug.llm_usage_breakdown.map((u, i) => (
                <div key={i} className="flex gap-2">
                  <span className="text-zinc-400">{u.model}</span>
                  <span>in:{u.input} out:{u.output}</span>
                  <span className="text-emerald-400">${(u.cost_usd ?? 0).toFixed(6)}</span>
                </div>
              ))}
            </div>
          )}
        </DebugSection>
      )}

      {(debug.reviewer_reasons?.length || debug.claim_to_citation_map) && (
        <DebugSection icon={<AlertTriangle size={13} />} title="Reviewer">
          <div className="space-y-2 text-zinc-400">
            {debug.reviewer_reasons && debug.reviewer_reasons.length > 0 && (
              <ul className="list-disc pl-4 space-y-0.5">
                {debug.reviewer_reasons.map((r, i) => <li key={i}>{r}</li>)}
              </ul>
            )}
            {debug.claim_to_citation_map && Object.keys(debug.claim_to_citation_map).length > 0 && (
              <div>
                <div className="mb-1 text-zinc-500">Claim → citations:</div>
                <div className="font-mono text-[11px] space-y-1">
                  {Object.entries(debug.claim_to_citation_map).slice(0, 5).map(([claim, ids]) => (
                    <div key={claim} className="break-words">
                      <span className="text-zinc-300">{claim.length > 50 ? claim.slice(0, 50) + '…' : claim}</span>
                      <span className="text-zinc-500"> → [{ids.join(', ')}]</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </DebugSection>
      )}

      {(debug.source_lang || debug.evidence_eval || debug.self_critic_regenerated || debug.final_polish_applied) && (
        <DebugSection icon={<Sparkles size={13} />} title="Archi v3">
          <div className="space-y-1.5 text-zinc-400">
            {debug.source_lang && (
              <div>Source lang: <span className="text-zinc-300">{debug.source_lang}</span></div>
            )}
            {debug.evidence_eval && (
              <div className="space-y-1">
                {debug.evidence_eval.relevance_score != null && (
                  <div>Relevance: <span className="text-zinc-300">{debug.evidence_eval.relevance_score.toFixed(2)}</span></div>
                )}
                {debug.evidence_eval.retry_needed != null && (
                  <div>Retry needed: <span className="text-zinc-300">{debug.evidence_eval.retry_needed ? 'Yes' : 'No'}</span></div>
                )}
                {debug.evidence_eval.coverage_gaps && debug.evidence_eval.coverage_gaps.length > 0 && (
                  <div>
                    <div className="mb-0.5">Coverage gaps:</div>
                    <ul className="list-disc pl-4 text-[11px]">
                      {debug.evidence_eval.coverage_gaps.map((g, i) => <li key={i}>{g}</li>)}
                    </ul>
                  </div>
                )}
              </div>
            )}
            {debug.self_critic_regenerated && (
              <div className="text-amber-400">Self-critic: regenerated</div>
            )}
            {debug.final_polish_applied && (
              <div className="text-violet-400">Final polish: applied</div>
            )}
          </div>
        </DebugSection>
      )}

      {debug.llm_call_log && debug.llm_call_log.length > 0 && (
        <DebugSection icon={<Brain size={13} />} title="LLM Call Log (prompts & responses)">
          <div className="space-y-4">
            {debug.llm_call_log.map((call, i) => (
              <div key={i} className="p-3 bg-black/20 rounded-xl border border-white/[0.06]">
                <div className="flex items-center gap-2 mb-2 text-zinc-400">
                  <span className="font-medium text-violet-400">{call.task}</span>
                  <span className="text-[10px]">{call.model}</span>
                  <span className="text-[10px]">in:{call.input_tokens} out:{call.output_tokens}</span>
                  <span className="text-emerald-500/80 text-[10px]">${call.cost_usd?.toFixed(6)}</span>
                </div>
                <div className="space-y-2 text-[11px]">
                  {call.messages?.map((m, j) => (
                    <div key={j}>
                      <div className="text-zinc-500 mb-0.5">{m.role}:</div>
                      <pre className="whitespace-pre-wrap break-words text-zinc-400 max-h-48 overflow-y-auto p-2 rounded bg-black/30">
                        {m.content}
                      </pre>
                    </div>
                  ))}
                  <div>
                    <div className="text-zinc-500 mb-0.5">Response:</div>
                    <pre className="whitespace-pre-wrap break-words text-zinc-300 max-h-48 overflow-y-auto p-2 rounded bg-black/30">
                      {call.response_content}
                    </pre>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </DebugSection>
      )}

      {debug.max_attempts_reached && (
        <div className="px-4 py-3 bg-amber-500/10 border-t border-white/[0.04] text-amber-400 text-xs flex items-center gap-2">
          <AlertTriangle size={12} />
          Max retrieval attempts reached
        </div>
      )}
    </div>
  )
}

function DebugSection({ icon, title, children }: { icon: React.ReactNode; title: string; children: React.ReactNode }) {
  return (
    <div className="px-4 py-3.5 border-b border-white/[0.04] last:border-b-0">
      <div className="flex items-center gap-2 text-zinc-500 font-medium mb-2.5">
        {icon}
        {title}
      </div>
      {children}
    </div>
  )
}

function StatPill({ label, value }: { label: string; value?: string | number | null }) {
  return (
    <div className="flex items-center gap-2 px-3 py-1.5 bg-black/20 rounded-lg border border-white/[0.04]">
      <span className="text-zinc-500">{label}</span>
      <span className="text-zinc-300 font-medium">{value ?? '-'}</span>
    </div>
  )
}
