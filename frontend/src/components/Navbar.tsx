/**
 * Navbar — shows active tenant and user info.
 */

import { Link, useNavigate } from 'react-router-dom'
import { authAPI } from '../services/api'
import type { User } from '../types'

interface NavbarProps {
  user: User | null;
}

function Navbar({ user }: NavbarProps) {
  const navigate = useNavigate()
  const storedTenant = authAPI.getActiveTenant()
  const freshTenant = user?.tenants?.find(t => t.id === storedTenant?.id)
  const activeTenant = (freshTenant && storedTenant)
    ? { ...storedTenant, document: freshTenant.document }
    : storedTenant

  const handleLogout = () => {
    authAPI.logout()
    navigate('/login', { replace: true })
  }

  const handleSwitchTenant = () => {
    // Clear the tenant header source before sending the user back to tenant selection.
    localStorage.removeItem('active_tenant_id')
    localStorage.removeItem('active_tenant')
    navigate('/select-tenant', { replace: true })
  }

  return (
    <nav className="navbar">
      <div className="navbar-container">
        <div className="navbar-brand">
          <Link to="/dashboard" className="navbar-logo">
            Buckets Explorer
          </Link>
          {activeTenant && (
            <span className="navbar-tenant" style={{
              marginLeft: '0.75rem', padding: '0.2rem 0.6rem',
              background: '#2563eb22', color: '#2563eb', borderRadius: '4px',
              fontSize: '0.8rem', fontWeight: 600, cursor: 'pointer',
            }} onClick={handleSwitchTenant} title="Click to switch area">
              {activeTenant.code}
            </span>
          )}
        </div>
        <div className="navbar-menu">
          {user && (
            <span className="navbar-user">
              {user.first_name && user.last_name
                ? `${user.first_name} ${user.last_name}`
                : user.display_name || user.username}
              {user.institution && ` (${user.institution})`}
            </span>
          )}
          <Link to="/dashboard" className="navbar-link">Dashboard</Link>
          <Link to="/profile" className="navbar-link">Profile</Link>
          {activeTenant?.document?.is_visible && activeTenant.document.tab_name && (
            <Link to="/guide" className="navbar-link">{activeTenant.document.tab_name}</Link>
          )}
          {user?.is_admin && (
            <Link to="/admin" className="navbar-link" style={{ color: '#f39c12' }}>Admin</Link>
          )}
          <button onClick={handleLogout} className="navbar-button">Logout</button>
        </div>
      </div>
    </nav>
  )
}

export default Navbar
