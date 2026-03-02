import { useState, useEffect } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { documents, admin, type Document, DOC_TYPES } from '../api/client'
import {
  Plus,
  Trash2,
  ExternalLink,
  ChevronLeft,
  ChevronRight,
  Loader2,
  FileText,
  Search,
  Filter,
  X,
  Layers,
  Download,
  Database,
  Upload,
  Globe,
  Sparkles,
} from 'lucide-react'

const DOC_TYPE_COLORS: Record<string, string> = {
  policy: 'text-blue-400 bg-blue-500/10 border-blue-500/15',
  tos: 'text-purple-400 bg-purple-500/10 border-purple-500/15',
  faq: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/15',
  howto: 'text-cyan-400 bg-cyan-500/10 border-cyan-500/15',
  pricing: 'text-amber-400 bg-amber-500/10 border-amber-500/15',
  other: 'text-zinc-400 bg-white/[0.03] border-white/[0.06]',
}

export default function DocumentList() {
  const navigate = useNavigate()
  const [items, setItems] = useState<Document[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [filterDocType, setFilterDocType] = useState<string>('')
  const [filterQ, setFilterQ] = useState('')
  const [filterQApplied, setFilterQApplied] = useState('')
  const [ingesting, setIngesting] = useState(false)
  const [ingestResult, setIngestResult] = useState<{ ok: number; skipped: number; error: number } | null>(null)
  const [showUploadModal, setShowUploadModal] = useState(false)
  const [showCrawlModal, setShowCrawlModal] = useState(false)
  const pageSize = 15

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await documents.list(page, pageSize, filterDocType || undefined, filterQApplied || undefined)
      setItems(res.items)
      setTotal(res.total)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load documents')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    setPage(1)
  }, [filterDocType, filterQApplied])

  useEffect(() => {
    load()
  }, [page, filterDocType, filterQApplied])

  useEffect(() => {
    if (!ingestResult) return
    const t = setTimeout(() => setIngestResult(null), 5000)
    return () => clearTimeout(t)
  }, [ingestResult])

  const handleDelete = async (id: string, e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (!confirm('Delete this document? Chunks and index will be removed.')) return
    try {
      await documents.delete(id)
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to delete')
    }
  }

  const handleIngestFromSource = async () => {
    if (!confirm('Ingest documents from source/ folder (custom_docs.json, sample_docs.json, sample_conversations.json, etc.)?')) return
    setIngesting(true)
    setError(null)
    setIngestResult(null)
    try {
      const res = await admin.ingestFromSource()
      setIngestResult(res.results ?? null)
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Ingest failed')
    } finally {
      setIngesting(false)
    }
  }

  const totalPages = Math.ceil(total / pageSize)

  return (
    <div className="animate-slide-up">
      <header className="flex justify-between items-start mb-8">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-white">Documents</h1>
          <p className="text-sm text-zinc-500 mt-1.5">Knowledge base documents for AI retrieval</p>
        </div>
        <div className="flex items-center gap-2.5">
          <button
            className="btn-ghost inline-flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed"
            onClick={handleIngestFromSource}
            disabled={ingesting}
            title="Load from source/custom_docs.json, sample_docs.json, sample_conversations.json, etc."
          >
            {ingesting ? <Loader2 size={15} className="animate-spin-slow" /> : <Database size={15} />}
            {ingesting ? 'Ingesting...' : 'Ingest from source'}
          </button>
          <button
            className="btn-ghost inline-flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium"
            onClick={() => setShowUploadModal(true)}
          >
            <Upload size={15} />
            Upload file
          </button>
          <button
            className="btn-ghost inline-flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium"
            onClick={() => setShowCrawlModal(true)}
            title="Crawl entire website and add all pages as documents"
          >
            <Globe size={15} />
            Crawl website
          </button>
          <button
            className="btn-primary inline-flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-medium"
            onClick={() => setShowCreateModal(true)}
          >
            <Plus size={16} />
            Add document
          </button>
        </div>
      </header>

      <div className="flex flex-wrap items-center gap-2.5 mb-5">
        <div className="relative">
          <Filter size={14} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-zinc-600 pointer-events-none" />
          <select
            value={filterDocType}
            onChange={(e) => setFilterDocType(e.target.value)}
            className="pl-9 pr-4 py-2.5 rounded-xl input-glass text-sm appearance-none min-w-[160px]"
            aria-label="Filter by type"
          >
            <option value="">All types</option>
            {DOC_TYPES.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </div>
        <div className="relative flex-1 max-w-xs">
          <Search size={14} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-zinc-600 pointer-events-none" />
          <input
            type="search"
            placeholder="Search title, URL..."
            value={filterQ}
            onChange={(e) => setFilterQ(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && setFilterQApplied(filterQ)}
            className="w-full pl-9 pr-4 py-2.5 rounded-xl input-glass text-sm"
            aria-label="Search"
          />
        </div>
        <button
          className="btn-ghost px-4 py-2.5 rounded-xl text-sm font-medium"
          onClick={() => setFilterQApplied(filterQ)}
        >
          Search
        </button>
        {(filterDocType || filterQApplied) && (
          <button
            className="px-3 py-2.5 rounded-xl text-xs text-zinc-600 hover:text-white hover:bg-white/[0.05] transition-colors"
            onClick={() => { setFilterDocType(''); setFilterQ(''); setFilterQApplied('') }}
          >
            Clear filters
          </button>
        )}
      </div>

      {error && (
        <div className="flex items-center gap-2 p-3.5 rounded-xl mb-5 bg-danger/10 border border-danger/20 text-red-300 text-sm animate-fade-in">
          {error}
        </div>
      )}
      {ingestResult && (
        <div className="flex items-center gap-2 p-3.5 rounded-xl mb-5 bg-emerald-500/10 border border-emerald-500/20 text-emerald-300 text-sm animate-fade-in">
          Ingest complete: {ingestResult.ok} added, {ingestResult.skipped} skipped, {ingestResult.error} errors
        </div>
      )}

      <div className="glass rounded-2xl overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center gap-3 py-20 text-zinc-500">
            <Loader2 size={20} className="animate-spin-slow text-accent" />
            <span className="text-sm">Loading documents...</span>
          </div>
        ) : items.length === 0 ? (
          <div className="flex flex-col items-center py-20 text-zinc-500">
            <div className="w-16 h-16 rounded-2xl glass-accent flex items-center justify-center mb-5 glow-sm">
              <FileText size={28} className="text-violet-400" />
            </div>
            <p className="font-semibold text-zinc-300 mb-1.5">No documents found</p>
            <p className="text-sm mb-5">
              {filterDocType || filterQApplied ? 'Try adjusting your filters' : 'Add your first document to get started'}
            </p>
            {!filterDocType && !filterQApplied && (
              <button
                className="btn-primary inline-flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-medium"
                onClick={() => setShowCreateModal(true)}
              >
                <Sparkles size={15} />
                Add document
              </button>
            )}
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/[0.04]">
                <th className="px-5 py-3.5 text-left text-zinc-500 font-medium text-xs uppercase tracking-wider">ID</th>
                <th className="px-5 py-3.5 text-left text-zinc-500 font-medium text-xs uppercase tracking-wider">Title</th>
                <th className="px-5 py-3.5 text-left text-zinc-500 font-medium text-xs uppercase tracking-wider">Type</th>
                <th className="px-5 py-3.5 text-left text-zinc-500 font-medium text-xs uppercase tracking-wider">Chunks</th>
                <th className="px-5 py-3.5 text-left text-zinc-500 font-medium text-xs uppercase tracking-wider">Updated</th>
                <th className="px-5 py-3.5 text-right text-zinc-500 font-medium text-xs uppercase tracking-wider">Actions</th>
              </tr>
            </thead>
            <tbody>
              {items.map((d) => (
                <tr
                  key={d.id}
                  className="border-b border-white/[0.03] last:border-b-0 hover:bg-white/[0.02] transition-colors duration-200 cursor-pointer group"
                  onClick={() => navigate(`/documents/${d.id}`)}
                >
                  <td className="px-5 py-4">
                    <code className="text-xs text-violet-400 bg-violet-500/10 px-2 py-1 rounded-lg font-mono">
                      {d.id.slice(0, 8)}
                    </code>
                  </td>
                  <td className="px-5 py-4">
                    <span className="text-zinc-200 font-medium">{d.title || '(Untitled)'}</span>
                  </td>
                  <td className="px-5 py-4">
                    <span className={`inline-flex items-center gap-1 px-2.5 py-1 text-xs font-medium rounded-lg border capitalize ${DOC_TYPE_COLORS[d.doc_type] || DOC_TYPE_COLORS.other}`}>
                      {d.doc_type}
                    </span>
                  </td>
                  <td className="px-5 py-4">
                    <span className="inline-flex items-center gap-1.5 text-zinc-400">
                      <Layers size={13} className="text-zinc-600" />
                      {d.chunks_count}
                    </span>
                  </td>
                  <td className="px-5 py-4 text-zinc-400">
                    {new Date(d.updated_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
                  </td>
                  <td className="px-5 py-4">
                    <div className="flex items-center justify-end gap-1.5 opacity-0 group-hover:opacity-100 transition-all duration-200">
                      <Link
                        to={`/documents/${d.id}`}
                        className="p-2 rounded-lg text-zinc-500 hover:text-white hover:bg-white/[0.06] transition-colors"
                        onClick={(e) => e.stopPropagation()}
                        title="View"
                      >
                        <ExternalLink size={14} />
                      </Link>
                      <button
                        className="p-2 rounded-lg text-zinc-500 hover:text-red-400 hover:bg-red-500/10 transition-colors"
                        onClick={(e) => handleDelete(d.id, e)}
                        title="Delete"
                      >
                        <Trash2 size={14} />
                      </button>
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
            {total} total · page {page} of {totalPages}
          </span>
          <div className="flex items-center gap-1">
            <button
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
              className="p-2 rounded-xl text-zinc-500 hover:text-white hover:bg-white/[0.05] disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
              disabled={page >= totalPages}
              onClick={() => setPage((p) => p + 1)}
            >
              <ChevronRight size={18} />
            </button>
          </div>
        </div>
      )}

      {showCreateModal && (
        <CreateDocumentModal
          onSuccess={(doc) => {
            setShowCreateModal(false)
            navigate(`/documents/${doc.id}`)
          }}
          onCancel={() => setShowCreateModal(false)}
        />
      )}
      {showUploadModal && (
        <UploadFileModal
          onSuccess={(doc) => {
            setShowUploadModal(false)
            load()
            navigate(`/documents/${doc.id}`)
          }}
          onCancel={() => setShowUploadModal(false)}
        />
      )}
      {showCrawlModal && (
        <CrawlWebsiteModal
          onSuccess={() => {
            setShowCrawlModal(false)
            load()
          }}
          onCancel={() => setShowCrawlModal(false)}
        />
      )}
    </div>
  )
}

function CreateDocumentModal({
  onSuccess,
  onCancel,
}: {
  onSuccess: (doc: Document) => void
  onCancel: () => void
}) {
  const [url, setUrl] = useState('')
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [docType, setDocType] = useState('other')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [fetching, setFetching] = useState(false)

  const handleFetchFromUrl = async () => {
    if (!url.trim()) {
      setError('Please enter URL first')
      return
    }
    setFetching(true)
    setError(null)
    try {
      const res = await documents.fetchFromUrl(url.trim())
      setTitle(res.title)
      setContent(res.content)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to fetch content from URL')
    } finally {
      setFetching(false)
    }
  }

  const handleSubmit = async () => {
    if (!url.trim()) {
      setError('Please enter URL')
      return
    }
    if (!content.trim()) {
      setError('Please enter content')
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      const doc = await documents.create({
        url: url.trim(),
        title: title.trim() || 'Untitled',
        content: content.trim(),
        doc_type: docType,
      })
      onSuccess(doc)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to create')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-md flex items-center justify-center z-[1000] p-4 animate-fade-in" onClick={onCancel}>
      <div
        className="glass rounded-2xl w-full max-w-[600px] max-h-[90vh] overflow-y-auto shadow-2xl animate-slide-up gradient-border"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex justify-between items-center px-6 py-5 border-b border-white/[0.04]">
          <h2 className="text-base font-semibold text-white">Add document</h2>
          <button className="p-2 rounded-xl text-zinc-500 hover:text-white hover:bg-white/[0.06] transition-colors" onClick={onCancel} aria-label="Close">
            <X size={18} />
          </button>
        </div>
        <div className="p-6 space-y-4">
          {error && <div className="p-3.5 rounded-xl bg-danger/10 border border-danger/20 text-red-300 text-sm">{error}</div>}
          <div>
            <label className="block text-sm font-medium text-zinc-400 mb-2">URL <span className="text-danger">*</span></label>
            <div className="flex gap-2.5">
              <input type="url" value={url} onChange={(e) => { setUrl(e.target.value); setError(null) }} placeholder="https://..." className="flex-1 px-4 py-2.5 rounded-xl input-glass text-sm" />
              <button
                type="button"
                onClick={handleFetchFromUrl}
                disabled={fetching || !url.trim()}
                className="btn-ghost shrink-0 inline-flex items-center gap-1.5 px-4 py-2.5 rounded-xl text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed"
                title="Auto fetch content from URL"
              >
                {fetching ? <Loader2 size={14} className="animate-spin-slow" /> : <Download size={14} />}
                {fetching ? 'Fetching...' : 'Fetch content'}
              </button>
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium text-zinc-400 mb-2">Title</label>
            <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Document title" className="w-full px-4 py-2.5 rounded-xl input-glass text-sm" />
          </div>
          <div>
            <label className="block text-sm font-medium text-zinc-400 mb-2">Type</label>
            <select value={docType} onChange={(e) => setDocType(e.target.value)} className="w-full px-4 py-2.5 rounded-xl input-glass text-sm" aria-label="Type">
              {DOC_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium text-zinc-400 mb-2">Content <span className="text-danger">*</span></label>
            <textarea value={content} onChange={(e) => setContent(e.target.value)} placeholder="Paste text or HTML content..." className="w-full px-4 py-2.5 rounded-xl input-glass text-sm" rows={6} />
          </div>
        </div>
        <div className="flex justify-end gap-2.5 px-6 py-5 border-t border-white/[0.04]">
          <button className="btn-ghost px-4 py-2.5 rounded-xl text-sm font-medium" onClick={onCancel}>Cancel</button>
          <button
            className="btn-primary inline-flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed"
            onClick={handleSubmit}
            disabled={submitting}
          >
            {submitting && <Loader2 size={14} className="animate-spin-slow" />}
            {submitting ? 'Processing...' : 'Add document'}
          </button>
        </div>
      </div>
    </div>
  )
}

function UploadFileModal({
  onSuccess,
  onCancel,
}: {
  onSuccess: (doc: Document) => void
  onCancel: () => void
}) {
  const [file, setFile] = useState<File | null>(null)
  const [title, setTitle] = useState('')
  const [docType, setDocType] = useState('other')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    setFile(f ?? null)
    setError(null)
    if (f) setTitle((prev) => prev || f.name.replace(/\.[^.]+$/, ''))
  }

  const handleSubmit = async () => {
    if (!file) {
      setError('Please select a file')
      return
    }
    const ext = file.name.toLowerCase().split('.').pop()
    if (!['txt', 'md', 'pdf'].includes(ext || '')) {
      setError('Only .txt, .md, and .pdf files are supported')
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      const doc = await documents.upload(file, {
        title: title.trim() || undefined,
        doc_type: docType,
      })
      onSuccess(doc)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Upload failed')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-md flex items-center justify-center z-[1000] p-4 animate-fade-in" onClick={onCancel}>
      <div className="glass rounded-2xl w-full max-w-[480px] shadow-2xl animate-slide-up gradient-border" onClick={(e) => e.stopPropagation()}>
        <div className="flex justify-between items-center px-6 py-5 border-b border-white/[0.04]">
          <h2 className="text-base font-semibold text-white">Upload file</h2>
          <button className="p-2 rounded-xl text-zinc-500 hover:text-white hover:bg-white/[0.06] transition-colors" onClick={onCancel} aria-label="Close">
            <X size={18} />
          </button>
        </div>
        <div className="p-6 space-y-4">
          {error && <div className="p-3.5 rounded-xl bg-danger/10 border border-danger/20 text-red-300 text-sm">{error}</div>}
          <div>
            <label className="block text-sm font-medium text-zinc-400 mb-2">File <span className="text-danger">*</span></label>
            <input
              type="file"
              accept=".txt,.md,.pdf"
              onChange={handleFileChange}
              className="block w-full text-sm text-zinc-300 file:mr-4 file:py-2.5 file:px-4 file:rounded-xl file:border-0 file:text-sm file:font-medium file:bg-violet-500/10 file:text-violet-400 hover:file:bg-violet-500/15 file:cursor-pointer file:transition-colors"
            />
            {file && (
              <p className="mt-2 text-xs text-zinc-500">{file.name} ({(file.size / 1024).toFixed(1)} KB)</p>
            )}
          </div>
          <div>
            <label className="block text-sm font-medium text-zinc-400 mb-2">Title</label>
            <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Document title (default: filename)" className="w-full px-4 py-2.5 rounded-xl input-glass text-sm" />
          </div>
          <div>
            <label className="block text-sm font-medium text-zinc-400 mb-2">Type</label>
            <select value={docType} onChange={(e) => setDocType(e.target.value)} className="w-full px-4 py-2.5 rounded-xl input-glass text-sm" aria-label="Type">
              {DOC_TYPES.map((t) => (<option key={t} value={t}>{t}</option>))}
            </select>
          </div>
        </div>
        <div className="flex justify-end gap-2.5 px-6 py-5 border-t border-white/[0.04]">
          <button className="btn-ghost px-4 py-2.5 rounded-xl text-sm font-medium" onClick={onCancel}>Cancel</button>
          <button
            className="btn-primary inline-flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed"
            onClick={handleSubmit}
            disabled={submitting || !file}
          >
            {submitting && <Loader2 size={14} className="animate-spin-slow" />}
            {submitting ? 'Processing...' : 'Upload'}
          </button>
        </div>
      </div>
    </div>
  )
}

function CrawlWebsiteModal({
  onSuccess,
  onCancel,
}: {
  onSuccess: () => void
  onCancel: () => void
}) {
  const [url, setUrl] = useState('')
  const [maxPages, setMaxPages] = useState(50)
  const [maxDepth, setMaxDepth] = useState(3)
  const [ingest, setIngest] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [crawling, setCrawling] = useState(false)
  const [result, setResult] = useState<{ pages_crawled: number; pages_ingested: number; pages: Array<{ url: string; title: string }> } | null>(null)

  const handleCrawl = async () => {
    if (!url.trim()) {
      setError('Please enter website URL')
      return
    }
    setCrawling(true)
    setError(null)
    setResult(null)
    try {
      const res = await documents.crawlWebsite({
        url: url.trim(),
        max_pages: maxPages,
        max_depth: maxDepth,
        ingest,
      })
      setResult({
        pages_crawled: res.pages_crawled,
        pages_ingested: res.pages_ingested,
        pages: res.pages,
      })
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Crawl failed')
    } finally {
      setCrawling(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-md flex items-center justify-center z-[1000] p-4 animate-fade-in" onClick={onCancel}>
      <div className="glass rounded-2xl w-full max-w-[560px] max-h-[90vh] overflow-y-auto shadow-2xl animate-slide-up gradient-border" onClick={(e) => e.stopPropagation()}>
        <div className="flex justify-between items-center px-6 py-5 border-b border-white/[0.04]">
          <h2 className="text-base font-semibold text-white">Crawl website</h2>
          <button className="p-2 rounded-xl text-zinc-500 hover:text-white hover:bg-white/[0.06] transition-colors" onClick={onCancel} aria-label="Close">
            <X size={18} />
          </button>
        </div>
        <div className="p-6 space-y-4">
          <p className="text-sm text-zinc-500">
            Crawl all pages on a website from a seed URL. Only pages on the same domain will be crawled.
          </p>
          {error && <div className="p-3.5 rounded-xl bg-danger/10 border border-danger/20 text-red-300 text-sm">{error}</div>}
          <div>
            <label className="block text-sm font-medium text-zinc-400 mb-2">Website URL <span className="text-danger">*</span></label>
            <input type="url" value={url} onChange={(e) => { setUrl(e.target.value); setError(null) }} placeholder="https://example.com" className="w-full px-4 py-2.5 rounded-xl input-glass text-sm" disabled={crawling} />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-zinc-400 mb-2">Max pages</label>
              <input type="number" min={1} max={500} value={maxPages} onChange={(e) => setMaxPages(Math.min(500, Math.max(1, parseInt(e.target.value, 10) || 50)))} className="w-full px-4 py-2.5 rounded-xl input-glass text-sm" disabled={crawling} />
            </div>
            <div>
              <label className="block text-sm font-medium text-zinc-400 mb-2">Max depth</label>
              <input type="number" min={1} max={10} value={maxDepth} onChange={(e) => setMaxDepth(Math.min(10, Math.max(1, parseInt(e.target.value, 10) || 3)))} className="w-full px-4 py-2.5 rounded-xl input-glass text-sm" disabled={crawling} />
            </div>
          </div>
          <label className="flex items-center gap-2.5 text-sm text-zinc-400 cursor-pointer">
            <input type="checkbox" checked={ingest} onChange={(e) => setIngest(e.target.checked)} disabled={crawling} className="rounded border-white/10 bg-transparent" />
            Ingest crawled pages into knowledge base
          </label>
          {result && (
            <div className="p-4 rounded-xl bg-emerald-500/10 border border-emerald-500/20 text-emerald-300 text-sm animate-fade-in">
              <p className="font-medium mb-1.5">Crawl complete</p>
              <p>Crawled <strong>{result.pages_crawled}</strong> page(s), ingested <strong>{result.pages_ingested}</strong>.</p>
              {result.pages.length > 0 && (
                <div className="mt-3 max-h-40 overflow-y-auto space-y-1 text-xs">
                  {result.pages.slice(0, 15).map((p) => (
                    <div key={p.url} className="truncate text-emerald-200/80" title={p.url}>{p.title || p.url}</div>
                  ))}
                  {result.pages.length > 15 && <div className="text-zinc-500">... and {result.pages.length - 15} more</div>}
                </div>
              )}
            </div>
          )}
        </div>
        <div className="flex justify-end gap-2.5 px-6 py-5 border-t border-white/[0.04]">
          <button className="btn-ghost px-4 py-2.5 rounded-xl text-sm font-medium" onClick={onCancel}>
            {result ? 'Close' : 'Cancel'}
          </button>
          {!result && (
            <button
              className="btn-primary inline-flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed"
              onClick={handleCrawl}
              disabled={crawling || !url.trim()}
            >
              {crawling && <Loader2 size={14} className="animate-spin-slow" />}
              {crawling ? 'Crawling... (may take a few minutes)' : 'Start crawl'}
            </button>
          )}
          {result && (
            <button className="btn-primary inline-flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-medium" onClick={onSuccess}>
              Done
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
