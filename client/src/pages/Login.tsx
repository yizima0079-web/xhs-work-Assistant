import { FormEvent, useState } from 'react'
import { api } from '../api/client'

export default function Login({ onSuccess }: { onSuccess: () => void }) {
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setLoading(true); setError(null)
    try { await api.login(username, password); onSuccess() }
    catch (e) { setError(e instanceof Error ? '账号或密码错误' : '登录失败') }
    finally { setLoading(false) }
  }
  return <main className="login-shell"><form className="login-card" onSubmit={submit}>
    <p className="eyebrow">DATAPP / ADMIN</p><h1>管理员登录</h1><p className="muted">登录后进入采集与可信内容看板</p>
    <label>账号<input className="field" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" /></label>
    <label>密码<input className="field" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" /></label>
    {error && <div className="error-banner">{error}</div>}
    <button className="primary" disabled={loading || !username || !password}>{loading ? '验证中…' : '登录'}</button>
  </form></main>
}
