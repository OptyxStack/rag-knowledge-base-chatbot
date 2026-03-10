import { useEffect, useState } from 'react'
import { admin, type CheckWhmcsCookiesResponse, type CrawlTicketsResponse } from '../api/client'
import { Loader2, Globe, Key, Shield, Database, LogIn, CheckCircle2, AlertCircle, Cookie, ExternalLink, Save, Link2 } from 'lucide-react'

const DEFAULT_LIST_PATH = 'supporttickets.php?filter=1'
const DEFAULT_LOGIN_PATH = 'login.php'

const COOKIE_EXAMPLE = `[
  {"name": "WHMCSxyz", "value": "abc123...", "domain": ".example.com", "path": "/"},
  {"name": "PHPSESSID", "value": "...", "domain": ".example.com", "path": "/"}
]`

export default function Crawler() {
  const [sessionCookies, setSessionCookies] = useState('')
  const [savingCookies, setSavingCookies] = useState(false)
  const [saveCookiesResult, setSaveCookiesResult] = useState<{ count: number } | null>(null)
  const [saveCookiesError, setSaveCookiesError] = useState<string | null>(null)
  const [cookiesStatus, setCookiesStatus] = useState<{ saved: boolean; count: number } | null>(null)
  const [checkingConnect, setCheckingConnect] = useState(false)
  const [connectResult, setConnectResult] = useState<{ ok: boolean; message: string; debug?: CheckWhmcsCookiesResponse['debug'] } | null>(null)

  const [mode, setMode] = useState<'cookies' | 'creds'>('cookies')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [totpCode, setTotpCode] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [listPath, setListPath] = useState(DEFAULT_LIST_PATH)
  const [loginPath, setLoginPath] = useState(DEFAULT_LOGIN_PATH)
  const [crawling, setCrawling] = useState(false)
  const [crawlResult, setCrawlResult] = useState<CrawlTicketsResponse | null>(null)
  const [crawlError, setCrawlError] = useState<string | null>(null)

  const parseCookies = (text: string): Array<{ name: string; value: string; domain?: string; path?: string }> | null => {
    try {
      const parsed = JSON.parse(text.trim())
      if (!Array.isArray(parsed)) return null
      return parsed.filter((c) => c && typeof c.name === 'string' && c.value != null)
    } catch {
      return null
    }
  }

  useEffect(() => {
    admin.getWhmcsCookies().then(setCookiesStatus).catch(() => setCookiesStatus({ saved: false, count: 0 }))
  }, [saveCookiesResult])

  useEffect(() => {
    admin
      .getWhmcsDefaults()
      .then((d) => {
        if (d.base_url) setBaseUrl(d.base_url)
        if (d.list_path) setListPath(d.list_path)
        if (d.login_path) setLoginPath(d.login_path)
      })
      .catch(() => {})
  }, [])

  const [checkDebug, setCheckDebug] = useState(false)

  const handleCheckConnect = async (useInlineCookies: boolean) => {
    setConnectResult(null)
    const cookies = useInlineCookies ? parseCookies(sessionCookies) : null
    if (useInlineCookies && (!cookies || cookies.length === 0)) {
      setConnectResult({ ok: false, message: 'Paste cookies into the field above first' })
      return
    }
    if (!useInlineCookies && (!cookiesStatus?.saved || (cookiesStatus?.count ?? 0) === 0)) {
      setConnectResult({ ok: false, message: 'Save cookies first (section 1)' })
      return
    }
    setCheckingConnect(true)
    try {
      const payload = {
        base_url: baseUrl.trim(),
        list_path: listPath.trim() || DEFAULT_LIST_PATH,
        debug: checkDebug,
        ...(cookies && cookies.length > 0 ? { session_cookies: cookies } : {}),
      }
      const res = await admin.checkWhmcsCookies(payload)
      setConnectResult({ ok: res.ok, message: res.message, debug: res.debug })
    } catch (e) {
      setConnectResult({ ok: false, message: e instanceof Error ? e.message : 'Check failed' })
    } finally {
      setCheckingConnect(false)
    }
  }

  const handleSaveCookies = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaveCookiesError(null)
    setSaveCookiesResult(null)
    setConnectResult(null)
    const cookies = parseCookies(sessionCookies)
    if (!cookies || cookies.length === 0) {
      setSaveCookiesError('Paste valid cookies JSON. Format: [{"name":"...","value":"...","domain":"...","path":"/"}]')
      return
    }
    setSavingCookies(true)
    try {
      const res = await admin.saveWhmcsCookies({ session_cookies: cookies })
      setSaveCookiesResult({ count: res.count })
      setSaveCookiesError(null)
    } catch (e) {
      setSaveCookiesError(e instanceof Error ? e.message : 'Failed to save cookies')
    } finally {
      setSavingCookies(false)
    }
  }

  const handleCrawl = async (e: React.FormEvent) => {
    e.preventDefault()
    setCrawlError(null)
    setCrawlResult(null)

    if (mode === 'creds') {
      if (!username.trim() || !password.trim()) {
        setCrawlError('Username and Password are required (note: may be blocked by CAPTCHA)')
        return
      }
    } else {
      if (!cookiesStatus?.saved || (cookiesStatus?.count ?? 0) === 0) {
        setCrawlError('Save cookies first (section 1) or switch to Username/Password')
        return
      }
    }

    if (!baseUrl.trim()) {
      setCrawlError('Enter base URL or configure WHMCS_BASE_URL in env')
      return
    }

    setCrawling(true)
    try {
      const payload: Parameters<typeof admin.crawlTickets>[0] = {
        base_url: baseUrl.trim(),
        list_path: listPath.trim() || DEFAULT_LIST_PATH,
        login_path: loginPath.trim() || DEFAULT_LOGIN_PATH,
      }
      if (mode === 'cookies') {
        payload.username = undefined
        payload.password = undefined
        payload.session_cookies = undefined
      } else {
        payload.username = username.trim()
        payload.password = password
        payload.totp_code = totpCode.trim() || undefined
      }
      const res = await admin.crawlTickets(payload)
      setCrawlResult(res)
    } catch (e) {
      setCrawlError(e instanceof Error ? e.message : 'Crawl failed')
    } finally {
      setCrawling(false)
    }
  }

  return (
    <div className="animate-slide-up max-w-2xl space-y-8">
      <header className="mb-2">
        <h1 className="text-2xl font-bold tracking-tight text-white">Crawl sample conversations (WHMCS)</h1>
        <p className="text-sm text-zinc-500 mt-1.5">
          Step 1: Save cookies after manual login. Step 2: Crawl conversation list.
        </p>
      </header>

      <section className="glass rounded-2xl p-6">
        <h2 className="text-lg font-semibold text-white flex items-center gap-2.5 mb-5">
          <div className="w-7 h-7 rounded-lg bg-violet-500/10 flex items-center justify-center">
            <Cookie size={15} className="text-violet-400" />
          </div>
          1. Save Session Cookies
        </h2>
        <p className="text-sm text-zinc-400 mb-4">
          <strong className="text-zinc-300">Method 1 – Login via browser (recommended):</strong> Run script on local machine, open browser to login, script will fetch cookies and send to API.
        </p>
        <p className="text-xs text-zinc-500 mb-2">First time: setup venv and install package</p>
        <div className="mb-2.5 p-4 rounded-xl bg-black/30 font-mono text-xs overflow-x-auto space-y-1 text-zinc-400 border border-white/[0.03]">
          <div className="text-zinc-600"># Windows (PowerShell)</div>
          <div>.\scripts\setup_login.ps1</div>
          <div className="mt-2 text-zinc-600"># Linux/Mac</div>
          <div>bash scripts/setup_login.sh</div>
        </div>
        <p className="text-xs text-zinc-500 mb-2">Then: activate venv and run script</p>
        <div className="mb-4 p-4 rounded-xl bg-black/30 font-mono text-xs overflow-x-auto space-y-1 text-zinc-400 border border-white/[0.03]">
          <div>.\.venv-login\Scripts\Activate.ps1   <span className="text-zinc-600"># Windows</span></div>
          <div>source .venv-login/bin/activate     <span className="text-zinc-600"># Linux/Mac</span></div>
          <div className="mt-2">python scripts/whmcs_login_browser.py --api-url http://localhost:8000/v1 --api-key &lt;YOUR_API_KEY&gt;</div>
        </div>
        <p className="text-xs text-zinc-500 mb-5">
          Run on local machine (not in Docker). API can run in Docker, use --api-url http://localhost:8000/v1.
        </p>
        <p className="text-sm text-zinc-400 mb-4">
          <strong className="text-zinc-300">Method 2 – Copy cookies manually:</strong>{' '}
          {baseUrl ? (
            <>
              <a
                href={`${baseUrl.replace(/\/$/, '')}/${loginPath}`}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 text-violet-400 hover:text-violet-300 transition-colors"
              >
                <ExternalLink size={13} />
                Open WHMCS login page
              </a>
              {' → '}
            </>
          ) : null}
          Login (solve CAPTCHA if any) → F12 → Application → Cookies → Copy → Paste JSON into field below
        </p>
        <form onSubmit={handleSaveCookies} className="space-y-4">
          <textarea
            value={sessionCookies}
            onChange={(e) => setSessionCookies(e.target.value)}
            placeholder={COOKIE_EXAMPLE}
            rows={5}
            className="w-full px-4 py-3 rounded-xl input-glass text-sm font-mono"
            disabled={savingCookies}
          />
          {saveCookiesError && (
            <div className="flex items-center gap-2.5 p-3.5 rounded-xl bg-danger/10 border border-danger/20 text-red-300 text-sm">
              <AlertCircle size={16} className="shrink-0" />
              {saveCookiesError}
            </div>
          )}
          <div className="flex gap-2.5 flex-wrap">
            <button
              type="submit"
              disabled={savingCookies}
              className="btn-primary inline-flex items-center justify-center gap-2 px-5 py-2.5 rounded-xl text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {savingCookies ? (
                <><Loader2 size={15} className="animate-spin" /> Saving...</>
              ) : (
                <><Save size={15} /> Save cookies</>
              )}
            </button>
            <button
              type="button"
              onClick={() => handleCheckConnect(true)}
              disabled={savingCookies || checkingConnect}
              className="btn-ghost inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {checkingConnect ? (
                <><Loader2 size={15} className="animate-spin" /> Checking...</>
              ) : (
                <><Link2 size={15} /> Check connect</>
              )}
            </button>
            <label className="inline-flex items-center gap-2 text-sm text-zinc-500 cursor-pointer">
              <input
                type="checkbox"
                checked={checkDebug}
                onChange={(e) => setCheckDebug(e.target.checked)}
                className="rounded border-white/10 bg-transparent"
              />
              Debug
            </label>
          </div>
        </form>
        {connectResult && (
          <div className="mt-4 space-y-2.5">
            <div className={`flex items-center gap-2.5 text-sm ${connectResult.ok ? 'text-emerald-400' : 'text-amber-400'}`}>
              {connectResult.ok ? <CheckCircle2 size={16} /> : <AlertCircle size={16} />}
              {connectResult.message}
            </div>
            {connectResult.debug && Object.keys(connectResult.debug).length > 0 && (
              <pre className="mt-2 p-4 rounded-xl bg-black/30 text-xs text-zinc-500 overflow-x-auto max-h-48 overflow-y-auto border border-white/[0.03]">
                {JSON.stringify(connectResult.debug, null, 2)}
              </pre>
            )}
          </div>
        )}
        {saveCookiesResult && (
          <div className="mt-4 flex items-center gap-2 text-emerald-400 text-sm">
            <CheckCircle2 size={16} />
            Saved {saveCookiesResult.count} cookies
          </div>
        )}
        {cookiesStatus?.saved && cookiesStatus.count > 0 && !saveCookiesResult && (
          <div className="mt-4 text-sm text-zinc-500">
            {cookiesStatus.count} cookies saved. You can crawl now (section 2).
          </div>
        )}
      </section>

      <section className="glass rounded-2xl p-6">
        <h2 className="text-lg font-semibold text-white flex items-center gap-2.5 mb-5">
          <div className="w-7 h-7 rounded-lg bg-blue-500/10 flex items-center justify-center">
            <Database size={15} className="text-blue-400" />
          </div>
          2. Crawl conversation list
        </h2>
        <p className="text-sm text-zinc-400 mb-5">
          Use saved cookies or Username/Password. Conversation list at{' '}
          {baseUrl ? (
            <a
              href={`${baseUrl.replace(/\/$/, '')}/${listPath}`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-violet-400 hover:text-violet-300 transition-colors"
            >
              {listPath}
            </a>
          ) : (
            <span>{listPath}</span>
          )}
        </p>

        <form onSubmit={handleCrawl} className="space-y-5">
          <div className="flex gap-1.5 p-1 rounded-xl bg-black/30 border border-white/[0.04]">
            <button
              type="button"
              onClick={() => setMode('cookies')}
              className={`flex-1 flex items-center justify-center gap-2 py-2.5 rounded-lg text-sm font-medium transition-all duration-200 ${
                mode === 'cookies' ? 'btn-primary' : 'text-zinc-500 hover:text-zinc-300'
              }`}
            >
              <Cookie size={15} />
              Use saved cookies
            </button>
            <button
              type="button"
              onClick={() => setMode('creds')}
              className={`flex-1 flex items-center justify-center gap-2 py-2.5 rounded-lg text-sm font-medium transition-all duration-200 ${
                mode === 'creds' ? 'btn-primary' : 'text-zinc-500 hover:text-zinc-300'
              }`}
            >
              <Key size={15} />
              Username / Password
            </button>
          </div>

          {mode === 'cookies' && (
            <div className="text-sm text-zinc-500">
              Will use {cookiesStatus?.count ?? 0} cookies saved in section 1.
            </div>
          )}

          {mode === 'creds' && (
            <>
              <div>
                <label className="flex items-center gap-2 text-sm font-medium text-zinc-300 mb-2">
                  <Key size={14} className="text-zinc-500" />
                  Username / Email
                </label>
                <input
                  type="text"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="your@email.com"
                  className="w-full px-4 py-2.5 rounded-xl input-glass text-sm"
                  autoComplete="username"
                  disabled={crawling}
                />
              </div>
              <div>
                <label className="flex items-center gap-2 text-sm font-medium text-zinc-300 mb-2">
                  <Key size={14} className="text-zinc-500" />
                  Password
                </label>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••"
                  className="w-full px-4 py-2.5 rounded-xl input-glass text-sm"
                  autoComplete="current-password"
                  disabled={crawling}
                />
              </div>
              <div>
                <label className="flex items-center gap-2 text-sm font-medium text-zinc-300 mb-2">
                  <Shield size={14} className="text-zinc-500" />
                  2FA Code (Authenticator)
                </label>
                <input
                  type="text"
                  value={totpCode}
                  onChange={(e) => setTotpCode(e.target.value.replace(/\D/g, '').slice(0, 8))}
                  placeholder="123456"
                  maxLength={8}
                  className="w-full px-4 py-2.5 rounded-xl input-glass text-sm font-mono tracking-widest"
                  autoComplete="one-time-code"
                  disabled={crawling}
                />
              </div>
            </>
          )}

          <div className="border-t border-white/[0.04] pt-5 space-y-4">
            <div>
              <label className="flex items-center gap-2 text-sm font-medium text-zinc-300 mb-2">
                <Globe size={14} className="text-zinc-500" />
                Base URL
              </label>
              <input
                type="url"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder="https://example.com/billing (or set WHMCS_BASE_URL in env)"
                className="w-full px-4 py-2.5 rounded-xl input-glass text-sm"
                disabled={crawling}
              />
            </div>
            <div>
              <label className="flex items-center gap-2 text-sm font-medium text-zinc-300 mb-2">
                <Database size={14} className="text-zinc-500" />
                List path (conversation list page)
              </label>
              <input
                type="text"
                value={listPath}
                onChange={(e) => setListPath(e.target.value)}
                placeholder={DEFAULT_LIST_PATH}
                className="w-full px-4 py-2.5 rounded-xl input-glass text-sm"
                disabled={crawling}
              />
              <p className="text-xs text-zinc-600 mt-1.5">Default: supporttickets.php?filter=1</p>
            </div>
            <div>
              <label className="flex items-center gap-2 text-sm font-medium text-zinc-300 mb-2">
                <LogIn size={14} className="text-zinc-500" />
                Login path
              </label>
              <input
                type="text"
                value={loginPath}
                onChange={(e) => setLoginPath(e.target.value)}
                placeholder={DEFAULT_LOGIN_PATH}
                className="w-full px-4 py-2.5 rounded-xl input-glass text-sm"
                disabled={crawling}
              />
            </div>
          </div>

          {crawlError && (
            <div className="flex items-center gap-2.5 p-3.5 rounded-xl bg-danger/10 border border-danger/20 text-red-300 text-sm">
              <AlertCircle size={16} className="shrink-0" />
              {crawlError}
            </div>
          )}

          <div className="flex gap-2.5 flex-wrap">
            <button
              type="button"
              onClick={() => handleCheckConnect(false)}
              disabled={crawling || checkingConnect || mode !== 'cookies' || !cookiesStatus?.saved}
              className="btn-ghost inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {checkingConnect ? (
                <><Loader2 size={15} className="animate-spin" /> Checking...</>
              ) : (
                <><Link2 size={15} /> Check connect</>
              )}
            </button>
            <button
              type="submit"
              disabled={crawling}
              className="btn-primary flex-1 inline-flex items-center justify-center gap-2 px-5 py-3 rounded-xl text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {crawling ? (
                <><Loader2 size={17} className="animate-spin" /> Crawling... (may take 2–5 minutes)</>
              ) : (
                <><Database size={17} /> Start crawl</>
              )}
            </button>
          </div>
        </form>
        {connectResult && (
          <div className="mt-4 space-y-2.5">
            <div className={`flex items-center gap-2.5 text-sm ${connectResult.ok ? 'text-emerald-400' : 'text-amber-400'}`}>
              {connectResult.ok ? <CheckCircle2 size={16} /> : <AlertCircle size={16} />}
              {connectResult.message}
            </div>
            {connectResult.debug && Object.keys(connectResult.debug).length > 0 && (
              <pre className="mt-2 p-4 rounded-xl bg-black/30 text-xs text-zinc-500 overflow-x-auto max-h-48 overflow-y-auto border border-white/[0.03]">
                {JSON.stringify(connectResult.debug, null, 2)}
              </pre>
            )}
          </div>
        )}

        {crawlResult && (
          <div className="mt-6 p-5 rounded-2xl bg-emerald-500/10 border border-emerald-500/15 animate-fade-in">
            <div className="flex items-center gap-2.5 text-emerald-400 font-semibold mb-2.5">
              <CheckCircle2 size={18} />
              Crawl complete
            </div>
            <p className="text-sm text-emerald-300/90">
              Saved <strong>{crawlResult.count}</strong> sample conversation(s) to{' '}
              {crawlResult.saved_to === 'database' ? (
                <span>database</span>
              ) : (
                <code className="text-xs bg-black/20 px-2 py-0.5 rounded-lg">{crawlResult.saved_to}</code>
              )}
              {crawlResult.skipped != null && crawlResult.skipped > 0 && (
                <span className="ml-2 text-amber-400">
                  (skipped {crawlResult.skipped} system alert tickets)
                </span>
              )}
            </p>
            <p className="text-sm text-zinc-500 mt-1.5">
              Go to Sample conversations page to approve and export to file (only approved items are used).
            </p>
            {crawlResult.tickets.length > 0 && (
              <div className="mt-3 max-h-48 overflow-y-auto rounded-xl bg-black/20 p-4 text-xs border border-white/[0.03]">
                {crawlResult.tickets.slice(0, 10).map((t) => (
                  <div key={t.external_id} className="py-2 border-b border-emerald-500/10 last:border-0 text-zinc-400">
                    <span className="text-zinc-600">#{t.external_id}</span>{' '}
                    {t.subject?.slice(0, 50)}
                    {t.subject && t.subject.length > 50 ? '…' : ''}
                  </div>
                ))}
                {crawlResult.tickets.length > 10 && (
                  <div className="py-2 text-zinc-600">
                    ... and {crawlResult.tickets.length - 10} more
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </section>
    </div>
  )
}
