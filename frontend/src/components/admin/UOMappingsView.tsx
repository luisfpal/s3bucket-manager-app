import { useState, useEffect } from 'react'
import { adminAPI, getApiError } from '../../services/api'
import type { AdminUOMapping } from '../../types'

function UOMappingsView() {
  const [mappings, setMappings] = useState<AdminUOMapping[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState('')

  useEffect(() => {
    (async () => {
      try {
        setMappings(await adminAPI.getUOMappings())
      } catch (err: unknown) {
        setError(getApiError(err, 'Failed to load UO mappings'))
      } finally {
        setLoading(false)
      }
    })()
  }, [])

  const filtered = mappings.filter((m) => {
    if (!filter) return true
    const q = filter.toLowerCase()
    return (
      m.uo_code.toLowerCase().includes(q) ||
      m.institution_name.toLowerCase().includes(q) ||
      m.tenant_code.toLowerCase().includes(q)
    )
  })

  if (loading) return <div className="admin-loading">Loading UO mappings...</div>

  return (
    <div>
      <div className="admin-page-header">
        <h1>NFFADI UO Mappings</h1>
        <input
          type="text"
          placeholder="Filter..."
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="admin-filter-input"
        />
      </div>
      <p style={{ color: '#666', marginBottom: '1rem', fontSize: '0.875rem' }}>
        Read-only. Updated via instruments CSV upload in the Sync panel.
      </p>
      {error && <div className="error-message" onClick={() => setError(null)}>{error}</div>}
      <div className="admin-table-wrap">
        <table className="admin-table">
          <thead>
            <tr>
              <th>UO Code</th>
              <th>Institution</th>
              <th>Tenant</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((m) => (
              <tr key={m.id}>
                <td className="admin-cell-mono">{m.uo_code}</td>
                <td>{m.institution_name}</td>
                <td>{m.tenant_code}</td>
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr><td colSpan={3} className="admin-empty">No UO mappings found</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="admin-table-footer">{filtered.length} of {mappings.length} mappings</div>
    </div>
  )
}

export default UOMappingsView
