import { useState, useEffect } from 'react'
import { adminAPI, getApiError } from '../../services/api'
import { formatDateTime, formatSize } from '../../utils/format'
import type { AdminTenant } from '../../types'

function TenantsView() {
  const [tenants, setTenants] = useState<AdminTenant[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    (async () => {
      try {
        setTenants(await adminAPI.getTenants())
      } catch (err: unknown) {
        setError(getApiError(err, 'Failed to load tenants'))
      } finally {
        setLoading(false)
      }
    })()
  }, [])

  if (loading) return <div className="admin-loading">Loading tenants...</div>

  return (
    <div>
      <div className="admin-page-header">
        <h1>Tenants</h1>
      </div>
      {error && <div className="error-message" onClick={() => setError(null)}>{error}</div>}
      <div className="admin-table-wrap">
        <table className="admin-table">
          <thead>
            <tr>
              <th>Code</th>
              <th>Members</th>
              <th>Buckets</th>
              <th>Storage</th>
              <th>Keys Updated</th>
            </tr>
          </thead>
          <tbody>
            {tenants.map((t) => (
              <tr key={t.id}>
                <td className="admin-cell-primary">{t.code}</td>
                <td>{t.member_count}</td>
                <td>{t.bucket_count}</td>
                <td>{formatSize(t.storage_bytes)}</td>
                <td>{t.mgmt_keys_updated_at ? formatDateTime(t.mgmt_keys_updated_at) : 'Never'}</td>
              </tr>
            ))}
            {tenants.length === 0 && (
              <tr><td colSpan={5} className="admin-empty">No tenants configured</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export default TenantsView
