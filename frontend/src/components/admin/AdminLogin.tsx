import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { adminAPI, authStorage, getApiError } from '../../services/api'

function AdminLogin() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const navigate = useNavigate()

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!username.trim() || !password) return
    try {
      setLoading(true)
      setError(null)
      const data = await adminAPI.login(username.trim(), password)
      authStorage.setAdminTokens(data.access, data.refresh, data.display_name || data.username)
      navigate('/admin', { replace: true })
    } catch (err: unknown) {
      setError(getApiError(err, 'Login failed'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="page-container">
      <div className="auth-container">
        <div className="auth-card">
          <h1 className="auth-title">Admin Login</h1>
          <p style={{ color: '#666', fontSize: '0.875rem', marginBottom: '0.5rem', textAlign: 'center' }}>S3 Bucket Manager</p>
          {error && <div className="error-message">{error}</div>}
          <form className="auth-form" onSubmit={handleSubmit}>
            <div className="form-group">
              <label>Username</label>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoFocus
                autoComplete="username"
              />
            </div>
            <div className="form-group">
              <label>Password</label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
              />
            </div>
            <button type="submit" className="auth-button" disabled={loading || !username.trim() || !password}>
              {loading ? 'Signing in...' : 'Sign In'}
            </button>
          </form>
        </div>
      </div>
    </div>
  )
}

export default AdminLogin
