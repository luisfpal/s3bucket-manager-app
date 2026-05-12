/**
 * Profile Page - Shows federated identity information.
 * Useful for debugging OAuth2 claims and GDPR transparency.
 */

import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { authAPI } from '../services/api'
import Navbar from './Navbar'
import type { User } from '../types'

function Profile() {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    authAPI.getCurrentUser()
      .then(setUser)
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [])

  if (loading) {
    return (
      <div className="page-container">
        <Navbar user={null} />
        <div className="loading-container"><h2>Loading...</h2></div>
      </div>
    )
  }

  if (!user) {
    return (
      <div className="page-container">
        <Navbar user={null} />
        <div className="error-container">
          <h2>Failed to load profile</h2>
          <Link to="/dashboard" className="button-primary" style={{ marginTop: '1rem', display: 'inline-block' }}>Back to Dashboard</Link>
        </div>
      </div>
    )
  }

  const fullName = `${user.first_name} ${user.last_name}`.trim()
  const nffadiRw = user.tenants?.find(t => t.code === 'NFFADI' && (t.role === 'rw' || t.role === 'admin'))

  const formatDate = (iso: string): string => {
    const d = new Date(iso)
    return `${String(d.getDate()).padStart(2, '0')}/${String(d.getMonth() + 1).padStart(2, '0')}/${d.getFullYear()}`
  }

  const fields = [
    { label: 'Name', value: fullName || user.display_name || user.username },
    { label: 'Email', value: user.email || '-' },
    ...(user.institution ? [{ label: 'Institution', value: user.institution }] : []),
    ...(nffadiRw?.uo_name
      ? [{ label: 'Organizational Unit', value: nffadiRw.uo_name }]
      : nffadiRw?.uo_code
        ? [{ label: 'UO Code', value: nffadiRw.uo_code }]
        : []
    ),
    { label: 'Tenants', value: user.tenants?.map(t => `${t.name} (${t.role})`).join(', ') || '-' },
    { label: 'Member Since', value: formatDate(user.date_joined) },
  ]

  return (
    <div className="page-container">
      <Navbar user={user} />

      <div className="dashboard-container">
        <h1 style={{ marginBottom: '2rem' }}>Your Identity Profile</h1>

        <div style={{ background: 'white', borderRadius: '8px', boxShadow: '0 2px 4px rgba(0,0,0,0.1)', overflow: 'hidden' }}>
          {user.profile_picture_url && (
            <div style={{ padding: '2rem', textAlign: 'center', background: '#f8f9fa' }}>
              <img
                src={user.profile_picture_url}
                alt="Profile"
                style={{ width: '80px', height: '80px', borderRadius: '50%', objectFit: 'cover' }}
              />
            </div>
          )}

          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <tbody>
              {fields.map(({ label, value }) => (
                <tr key={label} style={{ borderBottom: '1px solid #eee' }}>
                  <td style={{ padding: '1rem', fontWeight: 500, color: '#555', width: '200px' }}>
                    {label}
                  </td>
                  <td style={{ padding: '1rem', color: '#333' }}>
                    {value}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>


      </div>
    </div>
  )
}

export default Profile
