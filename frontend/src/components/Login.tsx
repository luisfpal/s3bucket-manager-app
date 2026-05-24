/**
 * Login Page
 *
 * Single button: "Login with Authentik"
 * No username/password form - all auth is external.
 */

import { useEffect } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { authAPI, authStorage } from '../services/api'
import BrandMark from './BrandMark'

const authErrorMessages: Record<string, string> = {
  oauth_state_missing: 'Your login session expired or the callback was opened without the original browser session. Start the login again from here.',
  oauth_state_invalid: 'The login state did not match this browser session. Start the login again from here.',
  oauth_forbidden: 'Your Authentik account is not a member of any registered group. Please contact the storage platform administrators to request access.',
  oauth_cancelled: 'The Authentik login was cancelled. Start the login again when ready.',
  oauth_failed: 'Authentik login failed. Start the login again from here.',
}

function Login() {
  const navigate = useNavigate()
  const location = useLocation()
  const authError = new URLSearchParams(location.search).get('auth_error') || ''

  useEffect(() => {
    if (authError) {
      authAPI.logout()
      return
    }
    if (authStorage.isAuthenticated()) {
      navigate('/dashboard', { replace: true })
    }
  }, [authError, navigate])

  if (!authError && authStorage.isAuthenticated()) {
    return null
  }

  return (
    <div className="page-container">
      <div className="auth-container">
        <div className="auth-card">
          <h1 className="auth-title">Buckets Explorer</h1>
          <BrandMark />

          {authError && (
            <div className="error-message" style={{ marginBottom: '1rem', textAlign: 'left' }}>
              {authErrorMessages[authError] || authErrorMessages.oauth_failed}
            </div>
          )}

          <button
            className="auth-button"
            style={{ width: '100%', fontSize: '1.1rem', padding: '1rem' }}
            onClick={() => authAPI.startLogin()}
          >
            Login with Authentik
          </button>
        </div>
      </div>
    </div>
  )
}

export default Login
