import { useEffect, useState } from 'react'
import Dashboard from './pages/Dashboard'
import KnowledgeBase from './pages/KnowledgeBase'
import Login from './pages/Login'
import { api } from './api/client'

export default function App() {
  const [authenticated, setAuthenticated] = useState<boolean | null>(null)
  const [view, setView] = useState<'dashboard' | 'knowledge'>('dashboard')
  // 知识库首次进入才挂载：view-shell-hidden 只是 display:none，不卸载组件，
  // 无条件挂载会让知识库的 load()（/kb/documents?limit=100）在首屏就跑，白等一次大请求。
  // 进去过之后保持挂载，来回切换不丢页内状态（搜索词、勾选、问答历史）。
  const [kbVisited, setKbVisited] = useState(false)
  useEffect(() => { api.session().then(() => setAuthenticated(true)).catch(() => setAuthenticated(false)) }, [])
  useEffect(() => { if (view === 'knowledge') setKbVisited(true) }, [view])
  if (authenticated === null) return <main className="login-shell"><p>验证登录状态…</p></main>
  if (!authenticated) return <Login onSuccess={() => setAuthenticated(true)} />
  return (
    <>
      <div className={view === 'dashboard' ? 'view-shell' : 'view-shell view-shell-hidden'} aria-hidden={view !== 'dashboard'}>
        <Dashboard onNav={() => setView('knowledge')} />
      </div>
      <div className={view === 'knowledge' ? 'view-shell' : 'view-shell view-shell-hidden'} aria-hidden={view !== 'knowledge'}>
        {kbVisited && <KnowledgeBase onBack={() => setView('dashboard')} />}
      </div>
    </>
  )
}
