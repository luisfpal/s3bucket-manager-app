/**
 * Login Page
 *
 * Single button: "Login with Authentik"
 * No username/password form - all auth is external.
 */

import { useNavigate } from 'react-router-dom'
import { authAPI, authStorage } from '../services/api'

function Login() {
  const navigate = useNavigate()

  if (authStorage.isAuthenticated()) {
    navigate('/dashboard', { replace: true })
    return null
  }

  return (
    <div className="page-container">
      <div className="auth-container">
        <div className="auth-card">
          <h1 className="auth-title">Bucket Manager</h1>

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
