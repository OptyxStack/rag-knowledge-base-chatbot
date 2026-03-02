import { useState } from 'react'
import { Routes, Route, Link, useLocation } from 'react-router-dom'
import {
  MessageSquare,
  FileText,
  BarChart3,
  Bot,
  Menu,
  X,
  Globe,
  Ticket,
  Sparkles,
  Settings as SettingsIcon,
} from 'lucide-react'
import ConversationList from './pages/ConversationList'
import ConversationDetail from './pages/ConversationDetail'
import DocumentList from './pages/DocumentList'
import DocumentDetail from './pages/DocumentDetail'
import Dashboard from './pages/Dashboard'
import Crawler from './pages/Crawler'
import TicketList from './pages/TicketList'
import TicketDetail from './pages/TicketDetail'
import Settings from './pages/Settings'

const NAV_ITEMS = [
  { to: '/', icon: MessageSquare, label: 'Conversations', match: ['/conversations'] },
  { to: '/tickets', icon: Ticket, label: 'Sample conversations', match: ['/tickets'] },
  { to: '/documents', icon: FileText, label: 'Documents', match: ['/documents'] },
  { to: '/crawler', icon: Globe, label: 'Crawl', match: ['/crawler'] },
  { to: '/dashboard', icon: BarChart3, label: 'Dashboard', match: ['/dashboard'] },
  { to: '/settings', icon: SettingsIcon, label: 'Settings', match: ['/settings'] },
]

function App() {
  const location = useLocation()
  const [sidebarOpen, setSidebarOpen] = useState(false)

  const isActive = (item: typeof NAV_ITEMS[0]) => {
    if (item.to === '/' && (location.pathname === '/' || location.pathname.startsWith('/conversations'))) return true
    return item.match.some((m) => location.pathname.startsWith(m))
  }

  return (
    <div className="flex min-h-screen relative">
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-black/60 backdrop-blur-sm z-40 lg:hidden animate-fade-in"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      <aside
        className={`
          fixed top-0 left-0 z-50 h-screen w-[270px]
          flex flex-col transition-transform duration-300 ease-out
          lg:translate-x-0 lg:static lg:z-auto
          ${sidebarOpen ? 'translate-x-0' : '-translate-x-full'}
        `}
        style={{
          background: 'linear-gradient(180deg, rgba(14,14,20,0.95) 0%, rgba(10,10,16,0.98) 100%)',
          borderRight: '1px solid rgba(255,255,255,0.05)',
        }}
      >
        <div className="absolute inset-0 overflow-hidden pointer-events-none">
          <div
            className="absolute -top-20 -left-20 w-60 h-60 rounded-full opacity-30"
            style={{ background: 'radial-gradient(circle, rgba(124,58,237,0.15) 0%, transparent 70%)' }}
          />
          <div
            className="absolute bottom-20 -right-10 w-40 h-40 rounded-full opacity-20"
            style={{ background: 'radial-gradient(circle, rgba(59,130,246,0.12) 0%, transparent 70%)' }}
          />
        </div>

        <div className="relative flex items-center gap-3 px-5 h-[72px] shrink-0">
          <div
            className="w-9 h-9 rounded-xl flex items-center justify-center glow-sm"
            style={{ background: 'linear-gradient(135deg, #7c3aed 0%, #6d28d9 100%)' }}
          >
            <Bot size={18} className="text-white" />
          </div>
          <div>
            <div className="font-semibold text-[14px] text-white leading-tight flex items-center gap-1.5">
              Support AI
              <Sparkles size={12} className="text-violet-400 opacity-70" />
            </div>
            <div className="text-[11px] text-zinc-500 leading-tight">Admin Console</div>
          </div>
        </div>

        <div className="relative mx-4 h-px bg-gradient-to-r from-transparent via-white/[0.06] to-transparent" />

        <nav className="relative flex-1 px-3 py-5 space-y-0.5">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon
            const active = isActive(item)
            return (
              <Link
                key={item.to}
                to={item.to}
                onClick={() => setSidebarOpen(false)}
                className={`
                  relative flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-[13px] font-medium transition-all duration-200
                  ${active
                    ? 'text-white'
                    : 'text-zinc-500 hover:text-zinc-300 hover:bg-white/[0.03]'
                  }
                `}
              >
                {active && (
                  <div
                    className="absolute inset-0 rounded-xl"
                    style={{
                      background: 'linear-gradient(135deg, rgba(124,58,237,0.15) 0%, rgba(59,130,246,0.08) 100%)',
                      border: '1px solid rgba(124,58,237,0.2)',
                    }}
                  />
                )}
                {active && (
                  <div
                    className="absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-5 rounded-r-full"
                    style={{ background: 'linear-gradient(180deg, #7c3aed, #3b82f6)' }}
                  />
                )}
                <Icon size={17} strokeWidth={active ? 2 : 1.5} className="relative z-10" />
                <span className="relative z-10">{item.label}</span>
              </Link>
            )
          })}
        </nav>

        <div className="relative mx-4 h-px bg-gradient-to-r from-transparent via-white/[0.06] to-transparent" />
        <div className="relative px-5 py-4">
          <div className="text-[11px] text-zinc-600">v1.0 · Auto Reply Chatbot</div>
        </div>
      </aside>

      <div className="flex-1 flex flex-col min-h-screen lg:ml-0 relative z-10">
        <header className="sticky top-0 z-30 flex items-center h-14 px-4 glass lg:hidden">
          <button
            className="p-2 -ml-2 rounded-xl text-zinc-500 hover:text-white hover:bg-white/[0.05]"
            onClick={() => setSidebarOpen(true)}
          >
            {sidebarOpen ? <X size={20} /> : <Menu size={20} />}
          </button>
          <div className="ml-3 flex items-center gap-2">
            <div
              className="w-6 h-6 rounded-lg flex items-center justify-center"
              style={{ background: 'linear-gradient(135deg, #7c3aed, #6d28d9)' }}
            >
              <Bot size={13} className="text-white" />
            </div>
            <span className="font-semibold text-sm text-white">Support AI</span>
          </div>
        </header>

        <main className="flex-1 p-4 md:p-6 lg:p-8 max-w-[1280px] w-full mx-auto animate-fade-in">
          <Routes>
            <Route path="/" element={<ConversationList />} />
            <Route path="/conversations/:id" element={<ConversationDetail />} />
            <Route path="/tickets" element={<TicketList />} />
            <Route path="/tickets/:id" element={<TicketDetail />} />
            <Route path="/documents" element={<DocumentList />} />
            <Route path="/documents/:id" element={<DocumentDetail />} />
            <Route path="/crawler" element={<Crawler />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/settings" element={<Settings />} />
          </Routes>
        </main>
      </div>
    </div>
  )
}

export default App
