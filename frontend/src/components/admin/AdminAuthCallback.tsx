import { useAutoError } from '../../hooks/useAutoMessage'
import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { adminAPI, authStorage, getApiError } from '../../services/api'

function AdminAuthCallback() {
  const navigate = useNavigate()
  const [error, setError] = useAutoError()

  useEffect(() => {
    const exchange = async () => {
      try {
        const data = await adminAPI.exchangeToken()
        authStorage.setAdminTokens(data.access, data.refresh, data.display_name || data.username)
        navigate('/admin', { replace: true })
      } catch (err: unknown) {
        console.error('Admin token exchange failed:', err)
        setError(getApiError(err, 'Authentication failed. Please try again.'))
      }
    }

    exchange()
  }, [navigate, setError])

  if (error) {
    return (
      <div className="page-container">
        <div className="auth-container">
          <div className="auth-card">
            <h2 style={{ color: '#e74c3c', marginBottom: '1rem' }}>Authentication Error</h2>
            <p style={{ color: '#666', marginBottom: '1.5rem' }}>{error}</p>
            <a href="/admin/login" className="auth-button" style={{ display: 'block', textAlign: 'center', textDecoration: 'none' }}>
              Back to Admin Panel
            </a>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="page-container">
      <div className="loading-container">
        <h2>Completing admin authentication...</h2>
        <p style={{ color: '#666' }}>Exchanging OAuth2 session for admin API tokens</p>
      </div>
    </div>
  )
}

export default AdminAuthCallback
