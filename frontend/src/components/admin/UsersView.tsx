import { useState, useEffect } from 'react'
import { adminAPI, getApiError } from '../../services/api'
import { formatDate } from '../../utils/format'
import type { AdminUser } from '../../types'

function UsersView() {
  const [users, setUsers] = useState<AdminUser[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState('')
  const [activeTenant, setActiveTenant] = useState<string | null>(null)
  const [uoFilter, setUoFilter] = useState<'all' | 'has' | 'none'>('all')

  useEffect(() => { load() }, [])

  const load = async () => {
    try {
      setLoading(true)
      setError(null)
      setUsers(await adminAPI.getUsers())
    } catch (err: unknown) {
      setError(getApiError(err, 'Failed to load users'))
    } finally {
      setLoading(false)
    }
  }

  const tenantCodes = [...new Set(users.map((u) => u.tenant_code).filter(Boolean))]

  const filtered = users.filter((u) => {
    if (activeTenant && u.tenant_code !== activeTenant) return false
    if (uoFilter === 'has' && !u.uo_code) return false
    if (uoFilter === 'none' && u.uo_code) return false
    if (!filter) return true
    const q = filter.toLowerCase()
    return (
      u.ceph_username.toLowerCase().includes(q) ||
      u.display_name.toLowerCase().includes(q) ||
      u.email.toLowerCase().includes(q) ||
      u.tenant_code.toLowerCase().includes(q)
    )
  })

  if (loading) return <div className="admin-loading">Loading users...</div>

  const chipStyle = { padding: '0.25rem 0.75rem', fontSize: '0.8rem' }

  return (
    <div>
      <div className="admin-page-header">
        <h1>Users</h1>
        <input
          type="text"
          placeholder="Filter..."
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="admin-filter-input"
        />
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem', marginBottom: '0.75rem' }}>
        {tenantCodes.length > 0 && (
          <div style={{ display: 'flex', gap: '0.25rem', flexWrap: 'wrap', alignItems: 'center' }}>
            <span style={{ fontSize: '0.75rem', color: '#888', marginRight: '0.25rem' }}>Tenant:</span>
            <button onClick={() => setActiveTenant(null)} className={activeTenant === null ? 'button-primary' : 'button-secondary'} style={chipStyle}>All</button>
            {tenantCodes.map((code) => (
              <button key={code} onClick={() => setActiveTenant(code)} className={activeTenant === code ? 'button-primary' : 'button-secondary'} style={chipStyle}>{code}</button>
            ))}
          </div>
        )}
        <div style={{ display: 'flex', gap: '0.25rem', flexWrap: 'wrap', alignItems: 'center' }}>
          <span style={{ fontSize: '0.75rem', color: '#888', marginRight: '0.25rem' }}>UO Code:</span>
          <button onClick={() => setUoFilter('all')} className={uoFilter === 'all' ? 'button-primary' : 'button-secondary'} style={chipStyle}>All</button>
          <button onClick={() => setUoFilter('has')} className={uoFilter === 'has' ? 'button-primary' : 'button-secondary'} style={chipStyle}>Has UO</button>
          <button onClick={() => setUoFilter('none')} className={uoFilter === 'none' ? 'button-primary' : 'button-secondary'} style={chipStyle}>No UO</button>
        </div>
      </div>

      {error && <div className="error-message" onClick={() => setError(null)}>{error}</div>}
      <div className="admin-table-wrap">
        <table className="admin-table">
          <thead>
            <tr>
              <th>User</th>
              <th>Email</th>
              <th>Tenant</th>
              <th>Role</th>
              <th>UO Code</th>
              <th>Last Login</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((u) => {
              return (
                <tr key={u.id}>
                  <td>
                    <div className="admin-cell-primary">{u.display_name}</div>
                    {u.ceph_username !== u.display_name && (
                      <div className="admin-cell-secondary">{u.ceph_username}</div>
                    )}
                  </td>
                  <td>{u.email || '\u2014'}</td>
                  <td>{u.tenant_code}</td>
                  <td><span className={`permission-badge permission-${u.role}`}>{u.role.toUpperCase()}</span></td>
                  <td>{u.uo_code || '\u2014'}</td>
                  <td>{u.last_login ? formatDate(u.last_login) : 'Never'}</td>
                </tr>
              )
            })}
            {filtered.length === 0 && (
              <tr><td colSpan={6} className="admin-empty">No users found</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="admin-table-footer">{filtered.length} of {users.length} users</div>
    </div>
  )
}

export default UsersView
