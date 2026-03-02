import axios, { type AxiosInstance } from 'axios'

// Use full backend URL in dev (e.g. VITE_API_BASE=http://localhost:8000/v1), relative /v1 in prod (nginx proxies)
const API_BASE =
  import.meta.env.VITE_API_BASE ||
  (import.meta.env.DEV ? 'http://localhost:8000/v1' : '/v1')
// Shared key for both API and Admin (X-API-Key + X-Admin-API-Key)
const API_KEY = import.meta.env.VITE_API_KEY || import.meta.env.VITE_ADMIN_API_KEY || 'dev-key'

const http: AxiosInstance = axios.create({
  baseURL: API_BASE,
  headers: {
    'Content-Type': 'application/json',
    'X-API-Key': API_KEY,
    'X-Admin-API-Key': API_KEY,
  },
})

http.interceptors.response.use(
  (res) => res,
  (err) => {
    const msg = err.response?.data?.detail || err.response?.data?.message || err.message || `HTTP ${err.response?.status}`
    return Promise.reject(new Error(typeof msg === 'string' ? msg : JSON.stringify(msg)))
  }
)

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const { method = 'GET', body, headers: optHeaders } = options
  const res = await http.request<T>({
    url: path,
    method: (method as string).toUpperCase() || 'GET',
    data: body ? (typeof body === 'string' ? JSON.parse(body) : body) : undefined,
    headers: optHeaders as Record<string, string> | undefined,
  })
  if (res.status === 204) return undefined as T
  return res.data as T
}

export type SourceType = 'ticket' | 'livechat'

export const conversations = {
  list: (page = 1, pageSize = 20, sourceType?: string, sourceId?: string) => {
    const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
    if (sourceType) params.set('source_type', sourceType)
    if (sourceId) params.set('source_id', sourceId)
    return api<{ items: Conversation[]; total: number; page: number; page_size: number }>(
      `/conversations?${params}`
    )
  },
  get: (id: string) =>
    api<ConversationDetail>(`/conversations/${id}`),
  create: (sourceType: SourceType, sourceId: string, metadata?: Record<string, unknown>) =>
    api<Conversation>(`/conversations`, {
      method: 'POST',
      body: JSON.stringify({
        source_type: sourceType,
        source_id: sourceId,
        metadata: metadata ?? {},
      }),
    }),
  update: (id: string, metadata: Record<string, unknown>) =>
    api<Conversation>(`/conversations/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ metadata }),
    }),
  delete: (id: string) =>
    api<void>(`/conversations/${id}`, { method: 'DELETE' }),
  sendMessage: (id: string, content: string) =>
    api<SendMessageResponse>(`/conversations/${id}/messages`, {
      method: 'POST',
      body: JSON.stringify({ content }),
    }),
}

export const dashboard = {
  stats: () => api<{ metrics: Record<string, number> }>('/dashboard/stats'),
}

export interface IngestFromSourceResponse {
  status: string
  message?: string
  results?: { ok: number; skipped: number; error: number }
  total?: number
}

export interface SaveWhmcsCookiesRequest {
  session_cookies: Array<{ name: string; value: string; domain?: string; path?: string }>
}

export interface CrawlTicketsRequest {
  username?: string
  password?: string
  totp_code?: string
  session_cookies?: Array<{ name: string; value: string; domain?: string; path?: string }>
  base_url?: string
  list_path?: string
  login_path?: string
}

export interface CrawlTicketsResponse {
  status: string
  count: number
  skipped?: number
  saved_to: string
  tickets: Array<{
    external_id: string
    subject: string
    description?: string
    status?: string
    priority?: string
    detail_url?: string
  }>
}

export interface SaveWhmcsCookiesResponse {
  status: string
  count: number
}

export interface WhmcsCookiesStatus {
  saved: boolean
  count: number
}

export interface CheckWhmcsCookiesRequest {
  session_cookies?: Array<{ name: string; value: string; domain?: string; path?: string }>
  base_url?: string
  list_path?: string
  debug?: boolean
}

export interface CheckWhmcsCookiesResponse {
  ok: boolean
  message: string
  debug?: {
    cookies_added?: Array<{ name: string; domain: string; path: string }>
    cookies_count?: number
    list_url?: string
    after_goto_base?: string
    final_url?: string
    page_title?: string
    redirected_to_login?: boolean
    has_login_form?: boolean
    error?: string
  }
}

export interface LLMConfig {
  llm_model: string
  llm_fallback_model: string
  llm_api_key: string
  llm_base_url: string
}

export interface LLMConfigUpdate {
  llm_model?: string
  llm_fallback_model?: string
  llm_api_key?: string
  llm_base_url?: string
}

export interface ArchiConfig {
  language_detect_enabled: boolean
  decision_router_use_llm: boolean
  evidence_evaluator_enabled: boolean
  self_critic_enabled: boolean
  final_polish_enabled: boolean
}

export interface ArchiConfigUpdate {
  language_detect_enabled?: boolean
  decision_router_use_llm?: boolean
  evidence_evaluator_enabled?: boolean
  self_critic_enabled?: boolean
  final_polish_enabled?: boolean
}

export const admin = {
  getLLMConfig: () => http.get<LLMConfig>(`/admin/config/llm`).then((res) => res.data),
  updateLLMConfig: (data: LLMConfigUpdate) =>
    http.put<LLMConfig>(`/admin/config/llm`, data).then((res) => res.data),
  getArchiConfig: () => http.get<ArchiConfig>(`/admin/config/archi`).then((res) => res.data),
  updateArchiConfig: (data: ArchiConfigUpdate) =>
    http.put<ArchiConfig>(`/admin/config/archi`, data).then((res) => res.data),
  refreshConfigCache: () =>
    http.post<{ status: string; message: string }>(`/admin/config/refresh-cache`).then((res) => res.data),
  ingestFromSource: (sourceDir = 'source') =>
    http.post<IngestFromSourceResponse>(`/admin/ingest-from-source`, null, {
      params: { source_dir: sourceDir },
    }).then((res) => res.data),
  saveWhmcsCookies: (data: SaveWhmcsCookiesRequest) =>
    http.post<SaveWhmcsCookiesResponse>(`/admin/save-whmcs-cookies`, data).then((res) => res.data),
  getWhmcsCookies: () =>
    http.get<WhmcsCookiesStatus>(`/admin/whmcs-cookies`).then((res) => res.data),
  checkWhmcsCookies: (data?: CheckWhmcsCookiesRequest) =>
    http.post<CheckWhmcsCookiesResponse>(`/admin/check-whmcs-cookies`, data ?? {}).then((res) => res.data),
  crawlTickets: (data: CrawlTicketsRequest) =>
    http.post<CrawlTicketsResponse>(`/admin/crawl-tickets`, data).then((res) => res.data),
  updateTicketApproval: (ticketId: string, approvalStatus: 'pending' | 'approved' | 'rejected') =>
    http.patch(`/admin/tickets/${ticketId}/approval`, { approval_status: approvalStatus }).then((res) => res.data),
  ingestTicketsToFile: () =>
    http.post<{ status: string; path: string; count: number }>(`/admin/ingest-tickets-to-file`).then((res) => res.data),
}

export const DOC_TYPES = ['policy', 'tos', 'faq', 'howto', 'pricing', 'other'] as const

export interface Document {
  id: string
  title: string
  source_url: string
  doc_type: string
  effective_date: string | null
  chunks_count: number
  source_file: string | null
  metadata: Record<string, unknown> | null
  raw_content?: string | null
  cleaned_content?: string | null
  created_at: string
  updated_at: string
}

export interface FetchFromUrlResponse {
  title: string
  content: string
  raw_html?: string | null
}

export interface CrawlWebsiteResponse {
  status: string
  pages_crawled: number
  pages_ingested: number
  pages: Array<{ url: string; title: string; doc_type: string }>
}

export const documents = {
  fetchFromUrl: (url: string) =>
    api<FetchFromUrlResponse>(`/documents/fetch-from-url`, {
      method: 'POST',
      body: JSON.stringify({ url }),
    }),
  list: (page = 1, pageSize = 20, docType?: string, q?: string) => {
    const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
    if (docType) params.set('doc_type', docType)
    if (q) params.set('q', q)
    return api<{ items: Document[]; total: number; page: number; page_size: number }>(
      `/documents?${params}`
    )
  },
  get: (id: string) => api<Document>(`/documents/${id}`),
  create: (data: {
    url: string
    title?: string
    content?: string
    raw_text?: string
    raw_html?: string
    doc_type?: string
    effective_date?: string
    metadata?: Record<string, unknown>
    source_file?: string
  }) =>
    api<Document>(`/documents`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  upload: (file: File, options?: { title?: string; doc_type?: string }) => {
    const fd = new FormData()
    fd.append('file', file)
    if (options?.title) fd.append('title', options.title)
    fd.append('doc_type', options?.doc_type ?? 'other')
    return http.post<Document>(`/documents/upload`, fd).then((r) => r.data)
  },
  update: (id: string, data: { title?: string; doc_type?: string; effective_date?: string; metadata?: Record<string, unknown> }) =>
    api<Document>(`/documents/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  delete: (id: string) =>
    api<void>(`/documents/${id}`, { method: 'DELETE' }),
  crawlWebsite: (params: {
    url: string
    max_pages?: number
    max_depth?: number
    ingest?: boolean
  }) =>
    api<CrawlWebsiteResponse>(`/documents/crawl-website`, {
      method: 'POST',
      body: JSON.stringify(params),
    }),
}

export interface Conversation {
  id: string
  source_type: string
  source_id: string
  metadata: Record<string, unknown> | null
  created_at: string
}

export interface FlowDebug {
  trace_id?: string
  attempt?: number
  model_used?: string
  decision?: string
  confidence?: number
  followup_questions?: string[]
  query_rewrite?: { keyword_query: string; semantic_query: string }
  retrieval_stats?: { bm25_count?: number; vector_count?: number; merged_count?: number; reranked_count?: number }
  evidence_summary?: { chunk_id: string; source_url: string; doc_type: string; score?: number; snippet: string }[]
  prompt_preview?: { system_length: number; user_length: number; system_preview: string; user_preview: string }
  llm_tokens?: { input: number; output: number }
  reviewer_reasons?: string[]
  max_attempts_reached?: boolean
  intent_cache?: string
  /** archi_v3: detected input language */
  source_lang?: string
  /** archi_v3: evidence evaluator result */
  evidence_eval?: { relevance_score?: number; retry_needed?: boolean; coverage_gaps?: string[] }
  /** archi_v3: answer was regenerated after self-critic fail */
  self_critic_regenerated?: boolean
  /** archi_v3: final polish was applied */
  final_polish_applied?: boolean
}

export interface Message {
  id: string
  role: string
  content: string
  created_at: string
  citations?: { chunk_id: string; source_url: string; doc_type: string }[]
  debug?: FlowDebug
}

export interface ConversationDetail extends Conversation {
  messages: Message[]
}

export interface SendMessageResponse {
  conversation_id: string
  message: {
    message_id: string
    content: string
    citations: { chunk_id: string; source_url: string; doc_type: string }[]
    confidence: number
    decision: string
    created_at: string
    debug?: FlowDebug
  }
}

export interface Ticket {
  id: string
  external_id: string
  subject: string
  description: string
  status: string
  priority: string | null
  email: string | null
  name: string | null
  approval_status: 'pending' | 'approved' | 'rejected'
  metadata: Record<string, unknown> | null
  source_file: string | null
  created_at: string | null
  updated_at: string | null
}

export interface TicketDetail extends Ticket {
  client_id: string | null
  detail_url: string | null
}

export const tickets = {
  list: (page = 1, pageSize = 20, status?: string, approvalStatus?: string, q?: string) => {
    const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
    if (status) params.set('status', status)
    if (approvalStatus) params.set('approval_status', approvalStatus)
    if (q) params.set('q', q)
    return api<{ items: Ticket[]; total: number; page: number; page_size: number }>(
      `/tickets?${params}`
    )
  },
  get: (id: string) => api<TicketDetail>(`/tickets/${id}`),
}
