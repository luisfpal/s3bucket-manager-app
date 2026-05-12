import { useState, useEffect } from 'react'
import { adminAPI, getApiError } from '../../services/api'
import type { AdminGroupMapping, AdminAvailableTenant } from '../../types'

function GroupMappingsView() {
  const [mappings, setMappings] = useState<AdminGroupMapping[]>([])
  const [available, setAvailable] = useState<AdminAvailableTenant[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [newGroup, setNewGroup] = useState('')
  const [newTenantId, setNewTenantId] = useState<number | ''>('')
  const [adding, setAdding] = useState(false)

  useEffect(() => { load() }, [])

  const load = async () => {
    try {
      setLoading(true)
      setError(null)
      const [m, a] = await Promise.all([
        adminAPI.getGroupMappings(),
        adminAPI.getAvailableTenants(),
      ])
      setMappings(m)
      setAvailable(a)
    } catch (err: unknown) {
      setError(getApiError(err, 'Failed to load mappings'))
    } finally {
      setLoading(false)
    }
  }

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!newGroup.trim() || newTenantId === '') return
    try {
      setAdding(true)
      setError(null)
      const mapping = await adminAPI.addGroupMapping(newGroup.trim(), newTenantId as number)
      setMappings((prev) => [...prev, mapping])
      setNewGroup('')
      setNewTenantId('')
    } catch (err: unknown) {
      setError(getApiError(err, 'Failed to add mapping'))
    } finally {
      setAdding(false)
    }
  }

  const handleDelete = async (id: number) => {
    if (!confirm('Remove this group mapping?')) return
    try {
      await adminAPI.deleteGroupMapping(id)
      setMappings((prev) => prev.filter((m) => m.id !== id))
    } catch (err: unknown) {
      setError(getApiError(err, 'Delete failed'))
    }
  }

  // Available tenants that have a Tenant record and don't already have a mapping
  const mappedTenantIds = new Set(mappings.map((m) => m.tenant_id))
  const selectableTenants = available.filter((a) => a.has_tenant && a.tenant_id && !mappedTenantIds.has(a.tenant_id))

  if (loading) return <div className="admin-loading">Loading group mappings...</div>

  return (
    <div>
      <div className="admin-page-header">
        <h1>Group-Tenant Mappings</h1>
      </div>
      {error && <div className="error-message" onClick={() => setError(null)}>{error}</div>}

      <form onSubmit={handleAdd} className="admin-add-form">
        <input
          type="text"
          placeholder="Type Authentik group name and select tenant to enable mapping"
          value={newGroup}
          onChange={(e) => setNewGroup(e.target.value)}
          className="admin-filter-input"
          style={{ flex: 1 }}
        />
        <select
          value={newTenantId}
          onChange={(e) => setNewTenantId(e.target.value ? Number(e.target.value) : '')}
          className="admin-select"
        >
          <option value="">Select tenant...</option>
          {selectableTenants.map((t) => (
            <option key={t.structure} value={t.tenant_id!}>{t.structure}</option>
          ))}
        </select>
        <button type="submit" className="button-primary" disabled={adding || !newGroup.trim() || newTenantId === ''}>
          {adding ? 'Adding...' : 'Add Mapping'}
        </button>
      </form>

      <div className="admin-table-wrap">
        <table className="admin-table">
          <thead>
            <tr>
              <th>Group</th>
              <th>Tenant</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {mappings.map((m) => (
              <tr key={m.id}>
                <td className="admin-cell-primary">{m.authentik_group}</td>
                <td>{m.tenant_code}</td>
                <td>
                  <button onClick={() => handleDelete(m.id)} className="button-delete-small">Remove</button>
                </td>
              </tr>
            ))}
            {mappings.length === 0 && (
              <tr><td colSpan={3} className="admin-empty">No group mappings configured</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {available.length > 0 && (
        <div style={{ marginTop: '1.5rem', color: '#666', fontSize: '0.875rem' }}>
          Available structures from RGWSquared: {available.map((a) => a.structure).join(', ')}
        </div>
      )}
    </div>
  )
}

export default GroupMappingsView
