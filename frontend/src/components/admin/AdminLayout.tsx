import { useEffect } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { adminAPI, authStorage } from '../../services/api'

const NAV_ITEMS = [
  { to: '/admin/buckets', label: 'Buckets' },
  { to: '/admin/users', label: 'Users' },
  { to: '/admin/group-mappings', label: 'Group Mappings' },
  { to: '/admin/uo-mappings', label: 'UO Mappings' },
  { to: '/admin/tenants', label: 'Tenants' },
  { to: '/admin/sync', label: 'Sync' },
]

function AdminLayout() {
  const navigate = useNavigate()
  const adminUser = authStorage.getAdminUsername()

  useEffect(() => {
    if (!adminAPI.isAuthenticated()) {
      navigate('/admin/login', { replace: true })
    }
  }, [navigate])

  const handleLogout = () => {
    adminAPI.logout()
    navigate('/admin/login', { replace: true })
  }

  if (!adminAPI.isAuthenticated()) return null

  return (
    <div className="admin-layout">
      <aside className="admin-sidebar">
        <div className="admin-sidebar-header">
          <h2>Bucket Manager</h2>
          <span className="admin-sidebar-subtitle">Admin Panel</span>
        </div>
        <nav className="admin-nav">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) => `admin-nav-link${isActive ? ' active' : ''}`}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="admin-sidebar-footer">
          <span className="admin-sidebar-user">{adminUser}</span>
          <button onClick={handleLogout} className="admin-logout-btn">Logout</button>
          <a href="/dashboard" className="admin-nav-link" style={{ marginTop: '0.5rem', fontSize: '0.8rem', opacity: 0.7 }}>
            Back to App
          </a>
        </div>
      </aside>
      <main className="admin-main">
        <Outlet />
      </main>
    </div>
  )
}

export default AdminLayout
