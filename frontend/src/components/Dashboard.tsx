import { useAutoError } from '../hooks/useAutoMessage'
/**
 * Dashboard — Tenant-scoped bucket listing.
 *
 * Two sections:
 * - Project Buckets (proposal): undeletable, from RGWSquared sync
 * - My Buckets (local): user-created, deletable by owner
 *
 * Permission-aware:
 * - RO users: no Create button, no delete
 * - RW users: can create local buckets, delete own local buckets
 */

import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { authAPI, bucketAPI, getApiError } from '../services/api'
import Navbar from './Navbar'
import { formatDate, formatSize } from '../utils/format'
import type { User, Bucket } from '../types'

function Dashboard() {
  const [user, setUser] = useState<User | null>(null)
  const [buckets, setBuckets] = useState<Bucket[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useAutoError()

  const [showModal, setShowModal] = useState(false)
  const [newBucketName, setNewBucketName] = useState('')
  const [newBucketDesc, setNewBucketDesc] = useState('')
  const [creating, setCreating] = useState(false)

  const [deletingBucket, setDeletingBucket] = useState<Bucket | null>(null)
  const [deleteConfirmText, setDeleteConfirmText] = useState('')
  const [deleting, setDeleting] = useState(false)

  const activeTenant = authAPI.getActiveTenant()
  const canWrite = activeTenant && activeTenant.role !== 'ro'

  useEffect(() => {
    loadData()
  }, [])

  const loadData = async () => {
    try {
      setLoading(true)
      const [userData, bucketData] = await Promise.all([
        authAPI.getCurrentUser(),
        bucketAPI.list(),
      ])
      setUser(userData)
      setBuckets(bucketData)
    } catch (err) {
      console.error('Failed to load data:', err)
      setError('Failed to load data. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  // Mirror backend PROJECT_ID_RE — catches invalid names before a server round-trip.
  const PROJECT_ID_RE = /^[a-z0-9][a-z0-9-]{0,48}[a-z0-9]$/

  const handleCreateBucket = async (e: React.FormEvent) => {
    e.preventDefault()
    const name = newBucketName.trim()
    if (!name) return

    if (!PROJECT_ID_RE.test(name)) {
      setError('Project ID must be 2–50 chars: lowercase letters, numbers, hyphens only. Cannot start or end with a hyphen.')
      return
    }

    try {
      setCreating(true)
      await bucketAPI.create(name, newBucketDesc.trim())
      setShowModal(false)
      setNewBucketName('')
      setNewBucketDesc('')
      await loadData()
    } catch (err: unknown) {
      setError(getApiError(err, 'Failed to create bucket'))
    } finally {
      setCreating(false)
    }
  }

  const handleDeleteBucket = (bucket: Bucket) => {
    setDeletingBucket(bucket)
    setDeleteConfirmText('')
  }

  const confirmDelete = async () => {
    if (!deletingBucket) return
    try {
      setDeleting(true)
      await bucketAPI.delete(deletingBucket.id)
      setDeletingBucket(null)
      setDeleteConfirmText('')
      await loadData()
    } catch (err: unknown) {
      setError(getApiError(err, 'Failed to delete bucket'))
      setDeletingBucket(null)
    } finally {
      setDeleting(false)
    }
  }

  const proposalBuckets = buckets.filter(b => b.bucket_type === 'proposal')
  const localBuckets = buckets.filter(b => b.bucket_type === 'local')

  if (loading) {
    return (
      <div className="page-container">
        <Navbar user={null} />
        <div className="loading-container"><h2>Loading...</h2></div>
      </div>
    )
  }

  const renderBucketTable = (bucketList: Bucket[], showDelete: boolean) => (
    <div className="files-table-container">
      <table className="files-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Permission</th>
            <th>Created</th>
            <th style={{ whiteSpace: 'nowrap' }}>Storage</th>
            <th style={{ textAlign: 'center' }}>Files</th>
            <th style={{ textAlign: 'center' }}>Shared</th>
            {showDelete && <th style={{ width: '60px' }}></th>}
          </tr>
        </thead>
        <tbody>
          {bucketList.map((bucket) => (
            <tr key={bucket.id} className="bucket-row">
              <td>
                <Link to={`/buckets/${bucket.id}`} className="bucket-row-name">
                  {bucket.display_name || bucket.name}
                </Link>
                {bucket.description && (
                  <span style={{ color: '#94a3b8', fontSize: '0.8rem', marginLeft: '0.75rem' }}>
                    {bucket.description}
                  </span>
                )}
              </td>
              <td>
                {bucket.permission && (
                  <span className={`permission-badge permission-${bucket.permission}`}>
                    {bucket.permission === 'owner' ? 'Owner' : bucket.permission === 'rw' ? 'RW' : 'RO'}
                  </span>
                )}
              </td>
              <td style={{ whiteSpace: 'nowrap', color: '#64748b' }}>
                {formatDate(bucket.created_at)}
              </td>
              <td style={{ whiteSpace: 'nowrap', color: '#64748b', fontSize: '0.875rem' }}>
                {bucket.size_bytes > 0 ? formatSize(bucket.size_bytes) : '—'}
              </td>
              <td style={{ textAlign: 'center', color: '#64748b', fontSize: '0.875rem' }}>
                {bucket.num_objects > 0 ? bucket.num_objects : '—'}
              </td>
              <td style={{ textAlign: 'center', color: '#64748b' }}>
                {bucket.shared_with_count > 0 ? bucket.shared_with_count : '-'}
              </td>
              {showDelete && (
                <td>
                  {bucket.is_deletable && bucket.permission === 'owner' && (
                    <button
                      className="button-delete-small"
                      onClick={() => handleDeleteBucket(bucket)}
                      style={{ padding: '0.25rem 0.5rem', fontSize: '0.75rem' }}
                    >
                      Delete
                    </button>
                  )}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )

  return (
    <div className="page-container">
      <Navbar user={user} />

      <div className="dashboard-container">
        {error && (
          <div className="error-message" onClick={() => setError(null)} style={{ cursor: 'pointer' }}>
            {error} (click to dismiss)
          </div>
        )}

        {buckets.length > 0 && (() => {
          const totalSize = buckets.reduce((sum, b) => sum + (b.size_bytes || 0), 0)
          const totalObjects = buckets.reduce((sum, b) => sum + (b.num_objects || 0), 0)  // used as totalFiles below
          return (
            <div style={{
              display: 'flex', gap: '2rem', padding: '0.75rem 1rem',
              background: '#f1f5f9', borderRadius: '0.5rem', marginBottom: '1rem',
              fontSize: '0.9rem', color: '#475569',
            }}>
              <span><strong>{buckets.length}</strong> buckets</span>
              <span><strong>{formatSize(totalSize)}</strong> storage used</span>
              <span><strong>{totalObjects}</strong> files</span>
            </div>
          )
        })()}

        {proposalBuckets.length > 0 && (
          <>
            <div className="dashboard-header">
              <h1>Proposals Buckets</h1>
            </div>
            {renderBucketTable(proposalBuckets, false)}
          </>
        )}

        <div className="dashboard-header" style={{ marginTop: proposalBuckets.length > 0 ? '1rem' : '0' }}>
          <h1>My Buckets</h1>
          {canWrite && (
            <button className="button-primary" onClick={() => setShowModal(true)}>
              + Create Bucket
            </button>
          )}
        </div>

        {localBuckets.length === 0 ? (
          <div className="empty-state">
            <h2>No local buckets yet</h2>
            {canWrite ? (
              <>
                <p className="mt-2">Create your first research bucket to get started.</p>
                <button className="button-primary mt-3" onClick={() => setShowModal(true)}>
                  Create Your First Bucket
                </button>
              </>
            ) : (
              <p className="mt-2">You have read-only access. Contact your administrator for write access.</p>
            )}
          </div>
        ) : (
          renderBucketTable(localBuckets, true)
        )}
      </div>

      {deletingBucket && (
        <div className="modal-overlay" onClick={() => !deleting && setDeletingBucket(null)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <h2 style={{ color: '#dc2626' }}>Delete Bucket</h2>
            <p style={{ color: '#6b7280', marginBottom: '0.5rem' }}>
              This will permanently remove <strong>{deletingBucket.display_name || deletingBucket.name}</strong> and all its files. This action cannot be undone.
            </p>
            <p style={{ marginBottom: '0.75rem', color: '#374151' }}>
              Type <strong>{deletingBucket.display_name || deletingBucket.name}</strong> to confirm:
            </p>
            <input
              type="text"
              className="modal-input"
              placeholder={deletingBucket.display_name || deletingBucket.name}
              value={deleteConfirmText}
              onChange={(e) => setDeleteConfirmText(e.target.value)}
              autoFocus
              disabled={deleting}
              autoComplete="off"
              data-lpignore="true"
            />
            <div className="modal-buttons">
              <button
                type="button"
                className="button-secondary"
                onClick={() => setDeletingBucket(null)}
                disabled={deleting}
              >
                Cancel
              </button>
              <button
                type="button"
                data-form-type="other"
                className="button-danger"
                onClick={confirmDelete}
                disabled={deleting || deleteConfirmText !== (deletingBucket.display_name || deletingBucket.name)}
              >
                {deleting ? 'Deleting...' : 'Delete Bucket'}
              </button>
            </div>
          </div>
        </div>
      )}

      {showModal && (
        <div className="modal-overlay" onClick={() => setShowModal(false)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <h2>Create New Bucket</h2>
            <p className="modal-hint">
              Enter a project ID (2-50 chars, lowercase letters, numbers, hyphens).
              The full bucket name will be generated automatically.
            </p>
            <form onSubmit={handleCreateBucket}>
              <input
                type="text"
                className="modal-input"
                placeholder="project-id"
                value={newBucketName}
                onChange={(e) => setNewBucketName(e.target.value)}
                autoFocus
              />
              <input
                type="text"
                className="modal-input"
                placeholder="Description (optional)"
                value={newBucketDesc}
                onChange={(e) => setNewBucketDesc(e.target.value)}
              />
              <div className="modal-buttons">
                <button type="button" className="button-secondary" onClick={() => setShowModal(false)}>
                  Cancel
                </button>
                <button type="submit" className="button-primary" disabled={creating || !newBucketName.trim()}>
                  {creating ? 'Creating...' : 'Create'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}

export default Dashboard
