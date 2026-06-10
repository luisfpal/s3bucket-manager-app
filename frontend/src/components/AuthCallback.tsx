import { useAutoError } from '../hooks/useAutoMessage'
/**
 * AuthCallback — OAuth2 Session-to-JWT Bridge
 *
 * After OAuth2 login:
 * 1. Exchange session cookie for JWT tokens
 * 2. Store tokens + tenant info
 * 3. If single tenant → auto-select → dashboard
 * 4. If multiple tenants → redirect to /select-tenant
 * 5. If no tenants → show "not provisioned" message
 */

import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { authAPI, authStorage, getApiError } from '../services/api'

function AuthCallback() {
  const navigate = useNavigate()
  const [error, setError] = useAutoError()

  useEffect(() => {
    const exchange = async () => {
      try {
        const data = await authAPI.exchangeToken()

        authStorage.setTokens(data.tokens.access, data.tokens.refresh)

        // Keep each browser session scoped to exactly one backend tenant.
        if (data.tenants.length === 0) {
          setError('Your account is not provisioned for any research area. Please contact your administrator.')
          return
        }

        if (data.active_tenant) {
          authAPI.setActiveTenant(data.active_tenant)
          navigate('/dashboard', { replace: true })
        } else if (data.tenant_selection_required) {
          localStorage.setItem('pending_tenants', JSON.stringify(data.tenants))
          navigate('/select-tenant', { replace: true })
        }
      } catch (err: unknown) {
        console.error('Token exchange failed:', err)
        setError(getApiError(err, 'Authentication failed. Please try again.'))
      }
    }

    exchange()
  }, [navigate])

  if (error) {
    return (
      <div className="page-container">
        <div className="auth-container">
          <div className="auth-card">
            <h2 style={{ color: '#e74c3c', marginBottom: '1rem' }}>Authentication Error</h2>
            <p style={{ color: '#666', marginBottom: '1.5rem' }}>{error}</p>
            <a href="/login" className="auth-button" style={{ display: 'block', textAlign: 'center', textDecoration: 'none' }}>
              Back to Login
            </a>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="page-container">
      <div className="loading-container">
        <h2>Completing authentication...</h2>
        <p style={{ color: '#666' }}>Exchanging OAuth2 session for API tokens</p>
      </div>
    </div>
  )
}

export default AuthCallback
