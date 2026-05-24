import { useAutoError } from '../hooks/useAutoMessage'
/**
 * ShareModal — Manage bucket sharing (local research buckets only).
 */

import { useState, useEffect } from 'react'
import { bucketAPI, getApiError } from '../services/api'
import type { BucketShare } from '../types'

interface ShareModalProps {
  bucketId: number;
  bucketName: string;
  onClose: () => void;
}

function ShareModal({ bucketId, bucketName, onClose }: ShareModalProps) {
  const [shares, setShares] = useState<BucketShare[]>([])
  const [username, setUsername] = useState('')
  const [permission, setPermission] = useState<'ro' | 'rw'>('ro')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useAutoError()
  const [adding, setAdding] = useState(false)
  const [updatingShareId, setUpdatingShareId] = useState<number | null>(null)

  useEffect(() => {
    loadShares()
  }, [])

  const loadShares = async () => {
    try {
      setLoading(true)
      const data = await bucketAPI.getShares(bucketId)
      setShares(data)
    } catch {
      setError('Failed to load shares')
    } finally {
      setLoading(false)
    }
  }

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!username.trim()) return
    try {
      setAdding(true)
      setError(null)
      await bucketAPI.addShare(bucketId, username.trim(), permission)
      setUsername('')
      await loadShares()
    } catch (err: unknown) {
      setError(getApiError(err, 'Failed to add share'))
    } finally {
      setAdding(false)
    }
  }

  const handlePermissionChange = async (shareId: number, shareUsername: string, newPerm: 'ro' | 'rw') => {
    try {
      setUpdatingShareId(shareId)
      setError(null)
      await bucketAPI.addShare(bucketId, shareUsername, newPerm)
      await loadShares()
    } catch (err: unknown) {
      setError(getApiError(err, 'Failed to update permission'))
    } finally {
      setUpdatingShareId(null)
    }
  }

  const handleRemove = async (shareId: number) => {
    try {
      await bucketAPI.removeShare(bucketId, shareId)
      await loadShares()
    } catch {
      setError('Failed to remove share')
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()} style={{ maxWidth: '550px' }}>
        <h2>Share: {bucketName}</h2>

        {error && (
          <div className="error-message" style={{ marginBottom: '1rem' }} onClick={() => setError(null)}>
            {error}
          </div>
        )}

        <form onSubmit={handleAdd} style={{ display: 'flex', gap: '0.5rem', marginBottom: '1.5rem' }}>
          <input
            type="text"
            className="modal-input"
            style={{ flex: 1, marginBottom: 0 }}
            placeholder="Email or display username (e.g. name.surname)"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
          <select
            value={permission}
            onChange={(e) => setPermission(e.target.value as 'ro' | 'rw')}
            style={{ padding: '0.5rem', borderRadius: '6px', border: '1px solid #ddd' }}
          >
            <option value="ro">Read-Only</option>
            <option value="rw">Read-Write</option>
          </select>
          <button type="submit" className="button-primary" disabled={adding || !username.trim()}>
            {adding ? 'Adding...' : 'Add'}
          </button>
        </form>

        {loading ? (
          <p style={{ color: '#888' }}>Loading...</p>
        ) : shares.length === 0 ? (
          <p style={{ color: '#888' }}>Not shared with anyone yet.</p>
        ) : (
          <div>
            {shares.map((share) => (
              <div key={share.id} style={{
                display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                padding: '0.5rem 0', borderBottom: '1px solid #eee',
              }}>
                <div>
                  <strong>{share.display_name || share.username}</strong>
                  {share.email && (
                    <span style={{ marginLeft: '0.5rem', fontSize: '0.85rem', color: '#888' }}>
                      {share.email}
                    </span>
                  )}
                </div>
                <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                  <select
                    value={share.permission}
                    onChange={(e) => handlePermissionChange(share.id, share.username, e.target.value as 'ro' | 'rw')}
                    disabled={updatingShareId === share.id}
                    style={{ padding: '0.25rem 0.5rem', borderRadius: '4px', border: '1px solid #ddd', fontSize: '0.85rem' }}
                  >
                    <option value="ro">RO</option>
                    <option value="rw">RW</option>
                  </select>
                  <button className="button-delete-small" onClick={() => handleRemove(share.id)}>
                    Remove
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        <div className="modal-buttons" style={{ marginTop: '1.5rem' }}>
          <button className="button-secondary" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  )
}

export default ShareModal
