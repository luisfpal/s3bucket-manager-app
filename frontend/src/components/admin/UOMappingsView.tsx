import { useAutoError } from '../../hooks/useAutoMessage'
import { useState, useEffect } from 'react'
import { adminAPI, getApiError } from '../../services/api'
import type { AdminUOMapping, AdminAvailableTenant } from '../../types'

function UOMappingsView() {
  const [tenants, setTenants] = useState<AdminAvailableTenant[]>([])
  const [selectedTenant, setSelectedTenant] = useState('')
  const [mappings, setMappings] = useState<AdminUOMapping[]>([])
  const [loading, setLoading] = useState(true)
  const [loadingMappings, setLoadingMappings] = useState(false)
  const [error, setError] = useAutoError()
  const [filter, setFilter] = useState('')

  // Load tenants that have UO mappings available
  useEffect(() => {
    (async () => {
      try {
        // Load all UO mappings once to determine which tenants have them
        const allMappings = await adminAPI.getUOMappings()
        const tenantsWithUO = [...new Set(allMappings.map(m => m.tenant_code))]
        // Also get available tenants for display names
        const available = await adminAPI.getAvailableTenants()
        // Filter to only tenants with UO mappings
        const filtered = available.filter(a => tenantsWithUO.includes(a.structure))
        setTenants(filtered)
        if (filtered.length > 0) setSelectedTenant(filtered[0].structure)
      } catch (err: unknown) {
        setError(getApiError(err, 'Failed to load UO mappings'))
      } finally {
        setLoading(false)
      }
    })()
  }, [])

  // Reload mappings when tenant changes
  useEffect(() => {
    if (!selectedTenant) return
    setLoadingMappings(true)
    adminAPI.getUOMappings()
      .then(all => setMappings(all.filter(m => m.tenant_code === selectedTenant)))
      .catch(err => setError(getApiError(err, 'Failed to load UO mappings')))
      .finally(() => setLoadingMappings(false))
  }, [selectedTenant])

  const filtered = mappings.filter(m => {
    if (!filter) return true
    const q = filter.toLowerCase()
    return m.uo_code.toLowerCase().includes(q) || m.institution_name.toLowerCase().includes(q)
  })

  if (loading) return <div className="admin-loading">Loading UO mappings...</div>

  return (
    <div>
      <div className="admin-page-header">
        <h1>UO Mappings</h1>
        <select
          value={selectedTenant}
          onChange={e => setSelectedTenant(e.target.value)}
          className="admin-select"
        >
          {tenants.length === 0 && <option value="">No tenants with UO mappings</option>}
          {tenants.map(t => (
            <option key={t.structure} value={t.structure}>{t.structure}</option>
          ))}
        </select>
        <input
          type="text"
          placeholder="Filter..."
          value={filter}
          onChange={e => setFilter(e.target.value)}
          className="admin-filter-input"
        />
      </div>

      <p style={{ color: '#6b7280', marginBottom: '1rem', fontSize: '0.875rem' }}>
        Read-only. Loaded from static fixture files at tenant activation. One fixture per tenant.
      </p>

      {error && <div className="error-message" onClick={() => setError(null)}>{error}</div>}

      {loadingMappings ? (
        <div className="admin-loading">Loading…</div>
      ) : (
        <div className="admin-table-wrap">
          <table className="admin-table">
            <thead>
              <tr>
                <th>UO Code</th>
                <th>Institution</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(m => (
                <tr key={m.id}>
                  <td className="admin-cell-mono">{m.uo_code}</td>
                  <td>{m.institution_name}</td>
                </tr>
              ))}
              {filtered.length === 0 && (
                <tr><td colSpan={2} className="admin-empty">No UO mappings found</td></tr>
              )}
            </tbody>
          </table>
          <div className="admin-table-footer">{filtered.length} of {mappings.length} mappings</div>
        </div>
      )}
    </div>
  )
}

export default UOMappingsView
