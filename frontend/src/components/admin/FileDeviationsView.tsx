import { useAutoError } from '../../hooks/useAutoMessage'
import React, { useState, useEffect } from 'react'
import { adminAPI, getApiError } from '../../services/api'
import type { FileDeviation, FileNameRule, AdminAvailableTenant } from '../../types'

function FileDeviationsView() {
  const [tenants, setTenants] = useState<AdminAvailableTenant[]>([])
  const [selectedTenant, setSelectedTenant] = useState('')
  const [deviations, setDeviations] = useState<FileDeviation[]>([])
  const [rules, setRules] = useState<FileNameRule[]>([])
  const [noRules, setNoRules] = useState(false)
  const [loading, setLoading] = useState(true)
  const [loadingData, setLoadingData] = useState(false)
  const [error, setError] = useAutoError()
  const [expandedUserId, setExpandedUserId] = useState<number | null>(null)

  useEffect(() => {
    adminAPI.getAvailableTenants()
      .then(data => {
        const active = data.filter(t => t.has_tenant)
        setTenants(active)
        if (active.length > 0) setSelectedTenant(active[0].structure)
      })
      .catch(err => setError(getApiError(err, 'Failed to load tenants')))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (!selectedTenant) return
    setLoadingData(true)
    setExpandedUserId(null)
    Promise.all([
      adminAPI.getFileDeviations(selectedTenant),
      adminAPI.getFileNameRules(selectedTenant),
    ])
      .then(([devResp, rulesResp]) => {
        setNoRules(devResp.no_rules)
        setDeviations(devResp.deviations)
        setRules(rulesResp)
      })
      .catch(err => setError(getApiError(err, 'Failed to load deviations')))
      .finally(() => setLoadingData(false))
  }, [selectedTenant])

  if (loading) return <div className="admin-loading">Loading…</div>

  const totalDeviatingFiles = deviations.reduce((sum, d) => sum + d.deviation_count, 0)

  return (
    <div>
      <div className="admin-page-header">
        <h1>File Name Deviations</h1>
        <select
          value={selectedTenant}
          onChange={e => setSelectedTenant(e.target.value)}
          className="admin-select"
        >
          <option value="">Select tenant…</option>
          {tenants.map(t => (
            <option key={t.structure} value={t.structure}>{t.structure}</option>
          ))}
        </select>
      </div>

      <p style={{ color: '#6b7280', marginBottom: '1rem', maxWidth: '640px' }}>
        Files whose names do not contain <em>any</em> of the tenant's required substrings.
        Click a user row to see their deviating filenames.
      </p>

      {error && <div className="error-message" onClick={() => setError(null)}>{error}</div>}

      {loadingData ? (
        <div className="admin-loading">Loading deviations…</div>
      ) : noRules ? (
        <div style={{ padding: '1rem 1.25rem', background: '#f0f9ff', border: '1px solid #bae6fd', borderRadius: '6px', color: '#0369a1' }}>
          No naming rules defined for <strong>{selectedTenant}</strong>.{' '}
          <a href="/admin/file-rules" style={{ color: '#2563eb' }}>Add rules in File Rules</a> to track deviations.
        </div>
      ) : deviations.length === 0 ? (
        <div style={{ padding: '1rem 1.25rem', background: '#f0fdf4', border: '1px solid #bbf7d0', borderRadius: '6px', color: '#166534', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <span style={{ fontSize: '1.2rem' }}>✓</span>
          All uploaded files comply with the naming rules for <strong>{selectedTenant}</strong>.
        </div>
      ) : (
        <>
          <div style={{
            display: 'flex', gap: '1.5rem', padding: '0.75rem 1rem',
            background: '#fef2f2', borderRadius: '0.5rem', marginBottom: '1rem',
            fontSize: '0.875rem', color: '#991b1b', border: '1px solid #fecaca',
          }}>
            <span><strong>{rules.length}</strong> rule{rules.length !== 1 ? 's' : ''}</span>
            <span><strong>{deviations.length}</strong> user{deviations.length !== 1 ? 's' : ''} with deviations</span>
            <span><strong>{totalDeviatingFiles}</strong> deviating file{totalDeviatingFiles !== 1 ? 's' : ''}</span>
          </div>

          <div style={{ marginBottom: '0.75rem', fontSize: '0.82rem', color: '#6b7280' }}>
            Rules: {rules.map(r => (
              <code key={r.id} style={{ background: '#f1f5f9', padding: '0.1rem 0.4rem', borderRadius: '4px', marginRight: '0.4rem' }}>
                {r.substring}
              </code>
            ))}
          </div>

          <div className="admin-table-wrap">
            <table className="admin-table">
              <thead>
                <tr>
                  <th style={{ width: '28px' }}></th>
                  <th>User</th>
                  <th>Tenant</th>
                  <th style={{ textAlign: 'right' }}>Deviating Files</th>
                </tr>
              </thead>
              <tbody>
                {deviations.map(d => {
                  const isExpanded = expandedUserId === d.user_id
                  return (
                    <React.Fragment key={d.user_id}>
                      <tr
                        style={{ cursor: 'pointer' }}
                        onClick={() => setExpandedUserId(isExpanded ? null : d.user_id)}
                      >
                        <td style={{ textAlign: 'center', color: '#94a3b8', userSelect: 'none' }}>
                          {isExpanded ? '▼' : '▶'}
                        </td>
                        <td>
                          <div className="admin-cell-primary">{d.display_name}</div>
                          <div className="admin-cell-secondary">{d.ceph_username}</div>
                        </td>
                        <td>{selectedTenant}</td>
                        <td style={{ textAlign: 'right' }}>
                          <span style={{
                            background: '#fee2e2', color: '#dc2626',
                            padding: '0.15rem 0.6rem', borderRadius: '12px',
                            fontSize: '0.8rem', fontWeight: 600,
                          }}>
                            {d.deviation_count}
                          </span>
                        </td>
                      </tr>
                      {isExpanded && (
                        <tr>
                          <td colSpan={4} style={{ padding: 0, background: '#fef9f9' }}>
                            <div style={{ padding: '0.6rem 1.5rem 0.6rem 2.5rem' }}>
                              <table style={{ width: '100%', fontSize: '0.82rem', borderCollapse: 'collapse' }}>
                                <thead>
                                  <tr style={{ background: '#fee2e2' }}>
                                    <th style={{ padding: '0.25rem 0.6rem', textAlign: 'left', fontWeight: 600 }}>File</th>
                                    <th style={{ padding: '0.25rem 0.6rem', textAlign: 'left', fontWeight: 600 }}>Bucket</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {d.files.map((f, i) => (
                                    <tr key={i} style={{ borderBottom: '1px solid #fecaca' }}>
                                      <td style={{ padding: '0.25rem 0.6rem', fontFamily: 'monospace', color: '#7f1d1d' }}>
                                        {f.file_key.split('/').pop() || f.file_key}
                                      </td>
                                      <td style={{ padding: '0.25rem 0.6rem', color: '#6b7280' }}>{f.bucket_name}</td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  )
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}

export default FileDeviationsView
