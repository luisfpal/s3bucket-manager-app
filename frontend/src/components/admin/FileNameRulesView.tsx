import { useAutoError } from '../../hooks/useAutoMessage'
import { useState, useEffect } from 'react'
import { adminAPI, getApiError } from '../../services/api'
import type { FileNameRule, AdminAvailableTenant } from '../../types'

function FileNameRulesView() {
  const [tenants, setTenants] = useState<AdminAvailableTenant[]>([])
  const [selectedTenant, setSelectedTenant] = useState('')
  const [rules, setRules] = useState<FileNameRule[]>([])
  const [loading, setLoading] = useState(true)
  const [loadingRules, setLoadingRules] = useState(false)
  const [error, setError] = useAutoError()
  const [newSubstring, setNewSubstring] = useState('')
  const [adding, setAdding] = useState(false)

  useEffect(() => {
    adminAPI.getAvailableTenants()
      .then(data => {
        setTenants(data.filter(t => t.has_tenant))
        if (data.length > 0) {
          const first = data.find(t => t.has_tenant)
          if (first) setSelectedTenant(first.structure)
        }
      })
      .catch(err => setError(getApiError(err, 'Failed to load tenants')))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (!selectedTenant) return
    setLoadingRules(true)
    adminAPI.getFileNameRules(selectedTenant)
      .then(setRules)
      .catch(err => setError(getApiError(err, 'Failed to load rules')))
      .finally(() => setLoadingRules(false))
  }, [selectedTenant])

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault()
    const sub = newSubstring.trim()
    if (!sub || !selectedTenant) return
    try {
      setAdding(true)
      setError(null)
      const rule = await adminAPI.addFileNameRule(selectedTenant, sub)
      setRules(prev => [...prev.filter(r => r.id !== rule.id), rule]
        .sort((a, b) => a.substring.localeCompare(b.substring)))
      setNewSubstring('')
    } catch (err: unknown) {
      setError(getApiError(err, 'Failed to add rule'))
    } finally {
      setAdding(false)
    }
  }

  const handleDelete = async (id: number) => {
    try {
      setError(null)
      await adminAPI.deleteFileNameRule(id)
      setRules(prev => prev.filter(r => r.id !== id))
    } catch (err: unknown) {
      setError(getApiError(err, 'Failed to delete rule'))
    }
  }

  if (loading) return <div className="admin-loading">Loading…</div>

  const activeTenants = tenants.filter(t => t.has_tenant)

  return (
    <div>
      <div className="admin-page-header">
        <h1>File Naming Rules</h1>
        <select
          value={selectedTenant}
          onChange={e => setSelectedTenant(e.target.value)}
          className="admin-select"
        >
          <option value="">Select tenant…</option>
          {activeTenants.map(t => (
            <option key={t.structure} value={t.structure}>{t.structure}</option>
          ))}
        </select>
      </div>

      <p style={{ color: '#6b7280', marginBottom: '1rem', maxWidth: '640px' }}>
        Define substrings that file names must contain for this tenant.
        A file <strong>deviates</strong> if its name contains <em>none</em> of the required substrings.
        Zero rules means <strong>no constraints</strong> — all filenames are accepted.
      </p>

      {error && <div className="error-message" onClick={() => setError(null)}>{error}</div>}

      {selectedTenant && (
        <>
          <form onSubmit={handleAdd} className="admin-add-form" style={{ marginBottom: '1.5rem' }}>
            <input
              type="text"
              placeholder="Required substring (e.g. .hdf5 or experiment)"
              value={newSubstring}
              onChange={e => setNewSubstring(e.target.value)}
              className="admin-filter-input"
              style={{ minWidth: '280px' }}
              disabled={adding}
            />
            <button
              type="submit"
              className="button-primary"
              disabled={adding || !newSubstring.trim()}
            >
              {adding ? 'Adding…' : 'Add Rule'}
            </button>
          </form>

          {loadingRules ? (
            <div className="admin-loading">Loading rules…</div>
          ) : rules.length === 0 ? (
            <div style={{
              padding: '1rem 1.25rem',
              background: '#f0f9ff',
              border: '1px solid #bae6fd',
              borderRadius: '6px',
              color: '#0369a1',
              fontSize: '0.9rem',
            }}>
              No constraints defined. All filenames are accepted for <strong>{selectedTenant}</strong>.
              Add substrings above to enforce naming conventions.
            </div>
          ) : (
            <div className="admin-table-wrap">
              <table className="admin-table">
                <thead>
                  <tr>
                    <th>Required Substring</th>
                    <th style={{ width: '80px' }}></th>
                  </tr>
                </thead>
                <tbody>
                  {rules.map(r => (
                    <tr key={r.id}>
                      <td>
                        <code style={{ background: '#f1f5f9', padding: '0.15rem 0.5rem', borderRadius: '4px', fontSize: '0.875rem' }}>
                          {r.substring}
                        </code>
                      </td>
                      <td>
                        <button
                          className="button-delete-small"
                          onClick={() => handleDelete(r.id)}
                          title="Remove rule"
                        >
                          ✕
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="admin-table-footer">{rules.length} rule{rules.length !== 1 ? 's' : ''}</div>
            </div>
          )}
        </>
      )}
    </div>
  )
}

export default FileNameRulesView
