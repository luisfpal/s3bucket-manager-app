import { useAutoError } from '../../hooks/useAutoMessage'
import { useEffect } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { adminAPI } from '../../services/api'
import BrandMark from '../BrandMark'

const authErrorMessages: Record<string, string> = {
  oauth_state_missing: 'Your login session expired or the callback was opened without the original browser session. Start the login again from here.',
  oauth_state_invalid: 'The login state did not match this browser session. Start the login again from here.',
  oauth_forbidden: 'Your Authentik account is not authorized for admin panel access. Contact a platform administrator.',
  admin_oauth_forbidden: 'Your Authentik account is not a member of the admin group required for this panel.',
  missing_email: 'Your Authentik account does not have an email address configured. Please contact the administrator to set your email in the Authentik user profile.',
  oauth_cancelled: 'The Authentik login was cancelled. Start the login again when ready.',
  oauth_failed: 'Authentik login failed. Start the login again from here.',
}

function AdminLogin() {
  const navigate = useNavigate()
  const location = useLocation()
  const [error, setError] = useAutoError()
  const authError = new URLSearchParams(location.search).get('auth_error') || ''

  useEffect(() => {
    if (authError) {
      adminAPI.logout()
      return
    }
    if (adminAPI.isAuthenticated()) {
      navigate('/admin', { replace: true })
    }
  }, [authError, navigate])

  if (!authError && adminAPI.isAuthenticated()) {
    return null
  }

  return (
    <div className="page-container">
      <div className="auth-container">
        <div className="auth-card">
          <h1 className="auth-title">Admin Panel</h1>
          <p style={{ color: '#666', fontSize: '0.875rem', marginBottom: '0.5rem', textAlign: 'center' }}>Buckets Explorer</p>
          <BrandMark />

          {(authError || error) && (
            <div className="error-message" style={{ marginBottom: '1rem', textAlign: 'left' }}>
              {authError ? (authErrorMessages[authError] || authErrorMessages.oauth_failed) : error}
            </div>
          )}

          <button
            className="auth-button"
            style={{ width: '100%', fontSize: '1.1rem', padding: '1rem' }}
            onClick={() => {
              setError(null)
              adminAPI.startLogin()
            }}
          >
            Login with Authentik
          </button>
        </div>
      </div>
    </div>
  )
}

export default AdminLogin
