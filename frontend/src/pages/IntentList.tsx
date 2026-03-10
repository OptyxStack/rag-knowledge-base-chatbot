import { useState, useEffect } from 'react'
import { admin, type Intent, type IntentCreate } from '../api/client'
import {
  Plus,
  Trash2,
  Edit2,
  Loader2,
  MessageSquare,
  ChevronDown,
  ChevronRight,
  Check,
  X,
} from 'lucide-react'

export default function IntentList() {
  const [items, setItems] = useState<Intent[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [expandedId, setExpandedId] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await admin.listIntents()
      setItems(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load intents')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  const handleDelete = async (id: string, e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (!confirm('Delete this intent? It will no longer match user queries.')) return
    try {
      await admin.deleteIntent(id)
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to delete')
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center gap-3 py-24 animate-fade-in">
        <Loader2 size={22} className="animate-spin-slow text-accent" />
        <span className="text-zinc-500">Loading intents...</span>
      </div>
    )
  }

  return (
    <div className="animate-slide-up">
      <header className="flex justify-between items-start mb-8">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-white">Intent Cache</h1>
          <p className="text-sm text-zinc-500 mt-1.5">
            Predefined answers for common queries (who am i, what can you do, hello, etc.)
          </p>
        </div>
        <button
          className="btn-primary inline-flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium"
          onClick={() => setShowCreateModal(true)}
        >
          <Plus size={16} />
          Add intent
        </button>
      </header>

      {error && (
        <div className="p-3.5 rounded-xl mb-5 bg-red-500/10 border border-red-500/20 text-red-300 text-sm">
          {error}
        </div>
      )}

      {items.length === 0 ? (
        <div className="flex flex-col items-center py-24 text-zinc-500 glass rounded-2xl">
          <MessageSquare size={40} className="mb-4 text-zinc-600" />
          <p className="font-semibold text-zinc-400 mb-1.5">No intents yet</p>
          <p className="text-sm mb-5">Add intents to return instant answers for common queries</p>
          <button
            className="btn-primary inline-flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm"
            onClick={() => setShowCreateModal(true)}
          >
            <Plus size={16} />
            Add first intent
          </button>
        </div>
      ) : (
        <div className="space-y-2">
          {items.map((intent) => (
            <div
              key={intent.id}
              className="glass rounded-xl overflow-hidden"
            >
              <div
                className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-white/[0.03] transition-colors"
                onClick={() => setExpandedId(expandedId === intent.id ? null : intent.id)}
              >
                <button className="p-1 text-zinc-500">
                  {expandedId === intent.id ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                </button>
                <span
                  className={`px-2.5 py-1 rounded-lg text-xs font-medium ${
                    intent.enabled ? 'bg-emerald-500/20 text-emerald-400' : 'bg-zinc-500/20 text-zinc-500'
                  }`}
                >
                  {intent.enabled ? 'On' : 'Off'}
                </span>
                <span className="font-mono text-sm text-violet-400">{intent.key}</span>
                <span className="text-zinc-600">·</span>
                <span className="text-sm text-zinc-400 truncate flex-1">
                  {intent.answer.slice(0, 60)}
                  {intent.answer.length > 60 ? '…' : ''}
                </span>
                <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                  <button
                    className="p-2 rounded-lg text-zinc-500 hover:text-white hover:bg-white/5"
                    onClick={() => setEditingId(editingId === intent.id ? null : intent.id)}
                  >
                    <Edit2 size={14} />
                  </button>
                  <button
                    className="p-2 rounded-lg text-zinc-500 hover:text-red-400 hover:bg-red-500/10"
                    onClick={(e) => handleDelete(intent.id, e)}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
              {expandedId === intent.id && (
                <div className="px-4 pb-4 pt-0 border-t border-white/[0.04] mt-0">
                  <div className="mt-3 space-y-3 text-sm">
                    <div>
                      <div className="text-zinc-500 text-xs mb-1">Patterns (regex)</div>
                      <pre className="font-mono text-xs text-zinc-300 bg-black/20 p-3 rounded-lg overflow-x-auto">
                        {intent.patterns}
                      </pre>
                    </div>
                    <div>
                      <div className="text-zinc-500 text-xs mb-1">Answer</div>
                      <div className="text-zinc-300 bg-black/20 p-3 rounded-lg whitespace-pre-wrap">
                        {intent.answer}
                      </div>
                    </div>
                  </div>
                </div>
              )}
              {editingId === intent.id && (
                <IntentEditForm
                  intent={intent}
                  onSave={() => {
                    setEditingId(null)
                    load()
                  }}
                  onCancel={() => setEditingId(null)}
                />
              )}
            </div>
          ))}
        </div>
      )}

      {showCreateModal && (
        <IntentCreateModal
          onClose={() => setShowCreateModal(false)}
          onCreated={() => {
            setShowCreateModal(false)
            load()
          }}
        />
      )}
    </div>
  )
}

function IntentEditForm({
  intent,
  onSave,
  onCancel,
}: {
  intent: Intent
  onSave: () => void
  onCancel: () => void
}) {
  const [patterns, setPatterns] = useState(intent.patterns)
  const [answer, setAnswer] = useState(intent.answer)
  const [enabled, setEnabled] = useState(intent.enabled)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setErr(null)
    setSaving(true)
    try {
      await admin.updateIntent(intent.id, { patterns, answer, enabled })
      onSave()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to update')
    } finally {
      setSaving(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="p-4 border-t border-white/[0.04] space-y-3">
      {err && <div className="text-red-400 text-sm">{err}</div>}
      <div>
        <label className="block text-xs text-zinc-500 mb-1">Patterns (regex)</label>
        <textarea
          value={patterns}
          onChange={(e) => setPatterns(e.target.value)}
          rows={2}
          className="w-full px-3 py-2 rounded-lg input-glass text-sm font-mono"
          required
        />
      </div>
      <div>
        <label className="block text-xs text-zinc-500 mb-1">Answer</label>
        <textarea
          value={answer}
          onChange={(e) => setAnswer(e.target.value)}
          rows={4}
          className="w-full px-3 py-2 rounded-lg input-glass text-sm"
          required
        />
      </div>
      <label className="flex items-center gap-2 text-sm text-zinc-400">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => setEnabled(e.target.checked)}
          className="rounded border-white/10"
        />
        Enabled
      </label>
      <div className="flex gap-2">
        <button type="submit" disabled={saving} className="btn-primary px-4 py-2 rounded-lg text-sm flex items-center gap-2">
          {saving ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
          Save
        </button>
        <button type="button" onClick={onCancel} className="px-4 py-2 rounded-lg text-sm text-zinc-400 hover:text-white">
          Cancel
        </button>
      </div>
    </form>
  )
}

function IntentCreateModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [key, setKey] = useState('')
  const [patterns, setPatterns] = useState('')
  const [answer, setAnswer] = useState('')
  const [enabled, setEnabled] = useState(true)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setErr(null)
    setSaving(true)
    try {
      const data: IntentCreate = { key: key.trim(), patterns, answer, enabled }
      await admin.createIntent(data)
      onCreated()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to create')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div
        className="glass rounded-2xl p-6 w-full max-w-lg max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex justify-between items-center mb-5">
          <h2 className="text-lg font-semibold text-white">Add intent</h2>
          <button onClick={onClose} className="p-2 rounded-lg text-zinc-500 hover:text-white">
            <X size={18} />
          </button>
        </div>
        <form onSubmit={handleSubmit} className="space-y-4">
          {err && <div className="text-red-400 text-sm">{err}</div>}
          <div>
            <label className="block text-xs text-zinc-500 mb-1">Key (unique id)</label>
            <input
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder="e.g. who_are_you"
              className="w-full px-3 py-2 rounded-lg input-glass text-sm font-mono"
              required
            />
          </div>
          <div>
            <label className="block text-xs text-zinc-500 mb-1">Patterns (regex, one per line or combined)</label>
            <textarea
              value={patterns}
              onChange={(e) => setPatterns(e.target.value)}
              placeholder={"\\b(who are you|what are you)\\b"}
              rows={3}
              className="w-full px-3 py-2 rounded-lg input-glass text-sm font-mono"
              required
            />
          </div>
          <div>
            <label className="block text-xs text-zinc-500 mb-1">Answer</label>
            <textarea
              value={answer}
              onChange={(e) => setAnswer(e.target.value)}
              placeholder="I'm the support assistant..."
              rows={4}
              className="w-full px-3 py-2 rounded-lg input-glass text-sm"
              required
            />
          </div>
          <label className="flex items-center gap-2 text-sm text-zinc-400">
            <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} className="rounded border-white/10" />
            Enabled
          </label>
          <div className="flex gap-2 pt-2">
            <button type="submit" disabled={saving} className="btn-primary px-4 py-2.5 rounded-xl text-sm flex items-center gap-2">
              {saving ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
              Create
            </button>
            <button type="button" onClick={onClose} className="px-4 py-2.5 rounded-xl text-sm text-zinc-400 hover:text-white">
              Cancel
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
