import { useAutoError } from '../../hooks/useAutoMessage'
import { useState, useEffect, Fragment } from 'react'
import { adminAPI, getApiError } from '../../services/api'
import { formatDate, formatSize } from '../../utils/format'
import type { AdminBucket, AdminPermission } from '../../types'

type SortField = 'name' | 'size_bytes' | 'num_objects' | 'shares_count' | 'created_at' | null
type SortDir = 'asc' | 'desc'

const SORT_LABELS: Record<string, string> = {
  name: 'Name',
  size_bytes: 'Storage',
  num_objects: 'Objects',
  shares_count: 'Shares',
  created_at: 'Created',
}

const COL_COUNT = 8

function BucketsView() {
  const [buckets, setBuckets] = useState<AdminBucket[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useAutoError()
  const [filter, setFilter] = useState('')
  const [deleting, setDeleting] = useState<number | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<AdminBucket | null>(null)
  const [confirmText, setConfirmText] = useState('')
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [activeTenant, setActiveTenant] = useState<string | null>(null)
  const [sortField, setSortField] = useState<SortField>('size_bytes')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [expandedBuckets, setExpandedBuckets] = useState<Set<number>>(new Set())
  const [allPerms, setAllPerms] = useState<AdminPermission[] | null>(null)
  const [permsLoading, setPermsLoading] = useState(false)

  useEffect(() => { load() }, [])

  const load = async () => {
    try {
      setLoading(true)
      setError(null)
      setBuckets(await adminAPI.getBuckets())
    } catch (err: unknown) {
      setError(getApiError(err, 'Failed to load buckets'))
    } finally {
      setLoading(false)
    }
  }

  const handleDeleteConfirm = async () => {
    if (!deleteTarget) return
    setDeleteError(null)
    try {
      setDeleting(deleteTarget.id)
      await adminAPI.deleteBucket(deleteTarget.id)
      setBuckets((prev) => prev.filter((b) => b.id !== deleteTarget.id))
      setDeleteTarget(null)
      setConfirmText('')
    } catch (err: unknown) {
      setDeleteError(getApiError(err, 'Delete failed'))
    } finally {
      setDeleting(null)
    }
  }

  const toggleSort = (field: NonNullable<SortField>) => {
    if (sortField === field) {
      if (sortDir === 'asc') setSortDir('desc')
      else { setSortField(null); setSortDir('asc') }
    } else {
      setSortField(field)
      setSortDir(field === 'size_bytes' ? 'desc' : 'asc')
    }
  }

  const sortIndicator = (field: NonNullable<SortField>) => {
    if (sortField !== field) return ' \u2195'
    return sortDir === 'asc' ? ' \u25B2' : ' \u25BC'
  }

  const toggleExpand = async (bucketId: number) => {
    setExpandedBuckets((prev) => {
      const next = new Set(prev)
      if (next.has(bucketId)) next.delete(bucketId)
      else next.add(bucketId)
      return next
    })
    if (!allPerms) {
      setPermsLoading(true)
      try {
        setAllPerms(await adminAPI.getPermissions())
      } catch { /* ignore */ }
      setPermsLoading(false)
    }
  }

  const getBucketPerms = (b: AdminBucket) =>
    (allPerms || []).filter((p) => p.bucket_ceph_name === b.name)

  const sortableThStyle = { cursor: 'pointer' as const, userSelect: 'none' as const }

  const tenantCodes = [...new Set(buckets.map((b) => b.tenant_code).filter(Boolean))]

  const filtered = buckets.filter((b) => {
    if (activeTenant && b.tenant_code !== activeTenant) return false
    if (!filter) return true
    const q = filter.toLowerCase()
    return (
      b.name.toLowerCase().includes(q) ||
      (b.display_name || '').toLowerCase().includes(q) ||
      (b.tenant_code || '').toLowerCase().includes(q)
    )
  })

  const bucketDisplayName = (bucket: AdminBucket) => bucket.display_name || bucket.name

  const sorted = sortField
    ? [...filtered].sort((a, b) => {
        let cmp: number
        if (sortField === 'name') {
          cmp = bucketDisplayName(a).localeCompare(bucketDisplayName(b))
        } else if (sortField === 'created_at') {
          cmp = new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
        } else {
          cmp = (a[sortField] as number) - (b[sortField] as number)
        }
        if (cmp === 0 && sortField !== 'name') {
          return bucketDisplayName(a).localeCompare(bucketDisplayName(b))
        }
        return sortDir === 'asc' ? cmp : -cmp
      })
    : filtered

  if (loading) return <div className="admin-loading">Loading buckets...</div>

  return (
    <div>
      <div className="admin-page-header">
        <h1>Buckets</h1>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <input
            type="text"
            placeholder="Filter..."
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="admin-filter-input"
          />
          <button onClick={load} className="button-primary">Reload</button>
        </div>
      </div>
      {tenantCodes.length > 0 && (
        <div style={{ display: 'flex', gap: '0.25rem', flexWrap: 'wrap', marginBottom: '0.75rem' }}>
          <button
            onClick={() => setActiveTenant(null)}
            className={activeTenant === null ? 'button-primary' : 'button-secondary'}
            style={{ padding: '0.25rem 0.75rem', fontSize: '0.8rem' }}
          >All</button>
          {tenantCodes.map((code) => (
            <button
              key={code}
              onClick={() => setActiveTenant(code)}
              className={activeTenant === code ? 'button-primary' : 'button-secondary'}
              style={{ padding: '0.25rem 0.75rem', fontSize: '0.8rem' }}
            >{code}</button>
          ))}
        </div>
      )}
      {error && <div className="error-message" onClick={() => setError(null)}>{error}</div>}
      <div className="admin-table-wrap">
        <table className="admin-table">
          <thead>
            <tr>
              <th style={sortableThStyle} onClick={() => toggleSort('name')}>
                {SORT_LABELS.name}{sortIndicator('name')}
              </th>
              <th>Tenant</th>
              <th>Type</th>
              <th style={sortableThStyle} onClick={() => toggleSort('size_bytes')}>
                {SORT_LABELS.size_bytes}{sortIndicator('size_bytes')}
              </th>
              <th style={sortableThStyle} onClick={() => toggleSort('num_objects')}>
                {SORT_LABELS.num_objects}{sortIndicator('num_objects')}
              </th>
              <th style={sortableThStyle} onClick={() => toggleSort('shares_count')}>
                {SORT_LABELS.shares_count}{sortIndicator('shares_count')}
              </th>
              <th style={sortableThStyle} onClick={() => toggleSort('created_at')}>
                {SORT_LABELS.created_at}{sortIndicator('created_at')}
              </th>
              <th style={{ width: '5rem' }}></th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((b) => {
              const isExpanded = expandedBuckets.has(b.id)
              const perms = isExpanded ? getBucketPerms(b) : []
              return (
                <Fragment key={b.id}>
                  <tr style={{ cursor: 'pointer' }} onClick={() => toggleExpand(b.id)}>
                    <td>
                      <div className="admin-cell-primary" style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                        <span style={{ fontSize: '0.7rem', color: '#94a3b8' }}>{isExpanded ? '\u25BC' : '\u25B6'}</span>
                        {b.display_name || b.name}
                      </div>
                      {b.tenant_code && (
                        <div className="admin-cell-secondary" style={{ marginLeft: '1.1rem' }}>{b.tenant_code}/{b.name}</div>
                      )}
                    </td>
                    <td>{b.tenant_code}</td>
                    <td>
                      <span className={`admin-type-badge admin-type-${b.bucket_type}`}>{b.bucket_type}</span>
                      {b.is_orphan && (
                        <span className="admin-orphan-badge">Orphan</span>
                      )}
                    </td>
                    <td>{formatSize(b.size_bytes)}</td>
                    <td>{b.num_objects}</td>
                    <td>{b.shares_count}</td>
                    <td>{formatDate(b.created_at)}</td>
                    <td>
                      {b.is_deletable && (
                        <button
                          onClick={(e) => { e.stopPropagation(); setDeleteTarget(b); setConfirmText(''); setDeleteError(null) }}
                          disabled={deleting === b.id}
                          className="button-delete-small"
                        >
                          {deleting === b.id ? '...' : 'Delete'}
                        </button>
                      )}
                    </td>
                  </tr>
                  {isExpanded && (
                    <tr>
                      <td colSpan={COL_COUNT} style={{ padding: '0 1rem 0.75rem 2.2rem', background: '#f8fafc' }}>
                        <div style={{ padding: '0.5rem 0.75rem', borderRadius: '6px', fontSize: '0.85rem' }}>
                          {permsLoading ? (
                            <span style={{ color: '#94a3b8' }}>Loading access...</span>
                          ) : perms.length === 0 ? (
                            <div style={{ color: '#94a3b8' }}>
                              {b.is_orphan ? (
                                <span>
                                  Orphan bucket — exists in RGW but was not created via the webapp.
                                  Delete here or via RGWSquared bucketDelete.
                                </span>
                              ) : (
                                <span>No access records</span>
                              )}
                            </div>
                          ) : (
                            <div>
                              <div style={{ fontWeight: 600, marginBottom: '0.3rem', color: '#475569' }}>
                                {perms.length} user{perms.length !== 1 ? 's' : ''} with access
                              </div>
                              {perms.map((p) => (
                                <div key={p.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '0.2rem 0', borderBottom: '1px solid #e2e8f0' }}>
                                  <span className="admin-user-inline">
                                    <span style={{ fontWeight: p.permission === 'owner' ? 600 : 400 }}>
                                      {p.ceph_username}
                                    </span>
                                    {p.email && (
                                      <span className="admin-cell-secondary">{p.email}</span>
                                    )}
                                  </span>
                                  <span style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                                    {(p.file_count > 0 || p.storage_bytes > 0) && (
                                      <span style={{ fontSize: '0.72rem', color: '#94a3b8' }}>
                                        {p.file_count} file{p.file_count !== 1 ? 's' : ''} · {formatSize(p.storage_bytes)}
                                      </span>
                                    )}
                                    <span style={{
                                      fontSize: '0.75rem',
                                      fontWeight: 500,
                                      color: p.permission === 'owner' ? '#2563eb' : p.permission === 'rw' ? '#059669' : '#6b7280',
                                    }}>
                                      {p.permission === 'owner' ? 'Owner' : p.permission === 'rw' ? 'RW' : 'RO'}
                                    </span>
                                  </span>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
            {sorted.length === 0 && (
              <tr><td colSpan={COL_COUNT} className="admin-empty">No buckets found</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="admin-table-footer">{sorted.length} of {buckets.length} buckets</div>

      {deleteTarget && (
        <div className="modal-overlay" onClick={() => { setDeleteTarget(null); setDeleteError(null) }}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <h2>Delete Bucket</h2>
            <p style={{ color: '#dc2626', fontWeight: 600, margin: '0.5rem 0' }}>
              This will permanently delete all files in &quot;{deleteTarget.display_name || deleteTarget.name}&quot;.
            </p>
            <p style={{ margin: '0.75rem 0 0.5rem' }}>
              Type <strong>{deleteTarget.name}</strong> to confirm:
            </p>
            <input
              type="text"
              className="modal-input"
              value={confirmText}
              onChange={(e) => setConfirmText(e.target.value)}
              placeholder={deleteTarget.name}
              autoFocus
            />
            <div className="modal-buttons">
              <button className="button-secondary" onClick={() => { setDeleteTarget(null); setDeleteError(null) }}>Cancel</button>
              <button
                className="button-delete-small"
                disabled={confirmText !== deleteTarget.name || deleting === deleteTarget.id || !!deleteError}
                onClick={handleDeleteConfirm}
                style={{ padding: '0.5rem 1.5rem' }}
              >
                {deleting === deleteTarget.id ? 'Deleting...' : 'Delete Forever'}
              </button>
            </div>
            {deleteError && (
              <div style={{ marginTop: '1rem', padding: '0.75rem', background: '#fef3c7', border: '1px solid #f59e0b', borderRadius: '6px' }}>
                <p style={{ margin: '0 0 0.4rem', fontWeight: 600, color: '#92400e', fontSize: '0.88rem' }}>
                  Delete failed
                </p>
                <p style={{ margin: '0 0 0.75rem', color: '#78350f', fontSize: '0.83rem' }}>
                  {deleteError}
                </p>
                <p style={{ margin: '0 0 0.75rem', color: '#78350f', fontSize: '0.83rem' }}>
                  Storage must be removed through RGWSquared before the database record can be deleted. Fix RGWSquared connectivity if needed, then retry.
                </p>
                <button
                  className="button-primary"
                  disabled={confirmText !== deleteTarget.name || deleting === deleteTarget.id}
                  onClick={handleDeleteConfirm}
                  style={{ width: '100%', padding: '0.5rem' }}
                >
                  {deleting === deleteTarget.id ? 'Retrying...' : 'Retry delete'}
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

export default BucketsView
