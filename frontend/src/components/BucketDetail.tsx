/**
 * BucketDetail — Permission-aware file browser.
 *
 * - RO users: can download only (no upload/delete buttons)
 * - RW users: can upload, delete own files
 * - Owner: can upload, delete any file, manage shares
 *
 * Shows who has access to the bucket (all users, not just owner).
 * Supports inline file viewing for images, text, PDF via FileViewer modal.
 */

import { useState, useEffect, useRef } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { authAPI, bucketAPI, getApiError } from '../services/api'
import Navbar from './Navbar'
import ShareModal from './ShareModal'
import FileViewer from './FileViewer'
import { formatDateTime, formatSize } from '../utils/format'
import type { User, BucketDetail as BucketDetailType, BucketAccessList } from '../types'

function BucketDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [user, setUser] = useState<User | null>(null)
  const [bucket, setBucket] = useState<BucketDetailType | null>(null)
  const [accessList, setAccessList] = useState<BucketAccessList | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadProgress, setUploadProgress] = useState<{ current: number; total: number } | null>(null)
  const [showShareModal, setShowShareModal] = useState(false)
  const [showAccess, setShowAccess] = useState(false)
  const [viewingFile, setViewingFile] = useState<{ key: string; size: number } | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    loadData()
  }, [id])

  const loadData = async () => {
    if (!id) return
    try {
      setLoading(true)
      const bucketId = parseInt(id)
      const [userData, bucketData] = await Promise.all([
        authAPI.getCurrentUser(),
        bucketAPI.get(bucketId),
      ])
      setUser(userData)
      setBucket(bucketData)

      // Access lists are supplementary; bucket contents should still load if this fails.
      try {
        const access = await bucketAPI.getAccessList(bucketId)
        setAccessList(access)
      } catch {
      }
    } catch (err) {
      console.error('Failed to load bucket:', err)
      setError('Failed to load bucket details.')
    } finally {
      setLoading(false)
    }
  }

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (!files || files.length === 0 || !id) return

    const fileArray = Array.from(files)
    const bucketIdNum = parseInt(id)

    try {
      setUploading(true)
      setError(null)

      if (fileArray.length === 1) {
        await bucketAPI.uploadFile(bucketIdNum, fileArray[0])
      } else {
        setUploadProgress({ current: 0, total: fileArray.length })
        const errors: string[] = []

        for (let i = 0; i < fileArray.length; i++) {
          setUploadProgress({ current: i + 1, total: fileArray.length })
          try {
            await bucketAPI.uploadFile(bucketIdNum, fileArray[i])
          } catch (err: unknown) {
            errors.push(`${fileArray[i].name}: ${getApiError(err, 'Upload failed')}`)
          }
        }

        if (errors.length > 0) {
          setError(`${errors.length} file(s) failed:\n${errors.join('\n')}`)
        }
      }

      await loadData()
    } catch (err: unknown) {
      setError(getApiError(err, 'Upload failed'))
    } finally {
      setUploading(false)
      setUploadProgress(null)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const handleDeleteFile = async (fileKey: string) => {
    if (!id || !confirm(`Delete "${fileKey}"?`)) return
    try {
      await bucketAPI.deleteFile(parseInt(id), fileKey)
      await loadData()
    } catch (err: unknown) {
      setError(getApiError(err, 'Failed to delete file'))
    }
  }

  const handleDownload = async (fileKey: string) => {
    if (!id) return
    try {
      await bucketAPI.downloadFile(parseInt(id), fileKey)
    } catch (err) {
      console.error('Download failed:', err)
      setError('Failed to download file.')
    }
  }

  const handleLeaveBucket = async () => {
    if (!id || !bucket) return
    if (!confirm(`Leave bucket "${bucket.display_name || bucket.name}"? You will lose access.`)) return
    try {
      await bucketAPI.leaveBucket(parseInt(id))
      navigate('/dashboard')
    } catch (err: unknown) {
      setError(getApiError(err, 'Failed to leave bucket'))
    }
  }

  const canUpload = bucket?.permission === 'rw' || bucket?.permission === 'owner'
  const isOwner = bucket?.permission === 'owner'
  const canLeave = bucket?.permission !== 'owner' && bucket?.bucket_type === 'local'

  if (loading) {
    return (
      <div className="page-container">
        <Navbar user={null} />
        <div className="loading-container"><h2>Loading...</h2></div>
      </div>
    )
  }

  if (!bucket) {
    return (
      <div className="page-container">
        <Navbar user={user} />
        <div className="error-container">
          <h2>Bucket not found</h2>
          <Link to="/dashboard" className="button-primary mt-2">Back to Dashboard</Link>
        </div>
      </div>
    )
  }

  const permLabel = (p: string) => p === 'owner' ? 'Owner' : p === 'rw' ? 'Read-Write' : 'Read-Only'
  const permColor = (p: string) => p === 'owner' ? '#2563eb' : p === 'rw' ? '#059669' : '#6b7280'

  return (
    <div className="page-container">
      <Navbar user={user} />

      <div className="bucket-detail-container">
        <Link to="/dashboard" className="breadcrumb">&larr; Back to Dashboard</Link>

        <div className="bucket-detail-header">
          <div>
            <h1>{bucket.display_name || bucket.name}</h1>
            {bucket.description && <p style={{ color: '#666' }}>{bucket.description}</p>}
            <div style={{ marginTop: '0.5rem', display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
              <span className={`permission-badge permission-${bucket.permission}`}>
                {bucket.permission === 'owner' ? 'Owner' : bucket.permission === 'rw' ? 'RW' : 'RO'}
              </span>
              <span style={{ fontSize: '0.85rem', color: '#888' }}>
                {bucket.bucket_type === 'proposal' ? 'Project Bucket' : 'Local Bucket'}
              </span>
            </div>
          </div>
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            {canLeave && (
              <button className="button-delete-small" onClick={handleLeaveBucket} style={{ padding: '0.4rem 1rem' }}>
                Leave Bucket
              </button>
            )}
            {isOwner && bucket.bucket_type === 'local' && (
              <button className="button-secondary" onClick={() => setShowShareModal(true)}>
                Share
              </button>
            )}
            {canUpload && (
              <div className="upload-section">
                <input
                  ref={fileInputRef}
                  type="file"
                  multiple
                  onChange={handleUpload}
                  style={{ display: 'none' }}
                  id="file-upload"
                />
                <label htmlFor="file-upload" className="button-primary" style={{ cursor: uploading ? 'default' : 'pointer' }}>
                  {uploading
                    ? uploadProgress
                      ? `Uploading ${uploadProgress.current}/${uploadProgress.total}...`
                      : 'Uploading...'
                    : 'Upload Files'}
                </label>
              </div>
            )}
          </div>
        </div>

        {accessList && accessList.access.length > 0 && (
          <div style={{
            marginBottom: '1rem',
            border: '1px solid #e2e8f0',
            borderRadius: '8px',
            overflow: 'hidden',
          }}>
            <button
              onClick={() => setShowAccess(!showAccess)}
              style={{
                width: '100%',
                padding: '0.6rem 1rem',
                background: '#f8fafc',
                border: 'none',
                cursor: 'pointer',
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                fontSize: '0.9rem',
                fontWeight: 500,
                color: '#334155',
              }}
            >
              <span>
                {bucket.bucket_type === 'proposal'
                  ? `Owned by: ${accessList.owner_label}`
                  : `Owner: ${accessList.owner_label}`
                }
                {' '}&middot;{' '}
                {accessList.access.length} user{accessList.access.length !== 1 ? 's' : ''} with access
              </span>
              <span style={{ fontSize: '0.75rem', color: '#94a3b8' }}>
                {showAccess ? '▲ Hide' : '▼ Show'}
              </span>
            </button>
            {showAccess && (
              <div style={{ padding: '0.5rem 1rem' }}>
                {accessList.access.map((entry) => (
                  <div
                    key={entry.user_id}
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                      padding: '0.4rem 0',
                      borderBottom: '1px solid #f1f5f9',
                    }}
                  >
                    <div>
                      <span style={{ fontWeight: entry.permission === 'owner' ? 600 : 400 }}>
                        {entry.display_name}
                      </span>
                      {entry.email && (
                        <span style={{ color: '#94a3b8', fontSize: '0.75rem', marginLeft: '0.4rem' }}>
                          {entry.email}
                        </span>
                      )}
                    </div>
                    <span style={{
                      fontSize: '0.75rem',
                      fontWeight: 500,
                      color: permColor(entry.permission),
                      padding: '0.15rem 0.5rem',
                      borderRadius: '4px',
                      background: entry.permission === 'owner' ? '#eff6ff' : entry.permission === 'rw' ? '#ecfdf5' : '#f9fafb',
                    }}>
                      {permLabel(entry.permission)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {error && (
          <div className="error-message" onClick={() => setError(null)} style={{ cursor: 'pointer' }}>
            {error} (click to dismiss)
          </div>
        )}

        {bucket.files.length === 0 ? (
          <div className="empty-state">
            <h2>No files yet</h2>
            {canUpload ? (
              <p className="mt-2">Upload a file to get started.</p>
            ) : (
              <p className="mt-2">This bucket is empty.</p>
            )}
          </div>
        ) : (
          <div className="files-table-container">
            <table className="files-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Size</th>
                  <th>Uploaded By</th>
                  <th>Last Modified</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {bucket.files.map((file) => (
                  <tr key={file.key}>
                    <td className="file-name">{file.key}</td>
                    <td>{formatSize(file.size)}</td>
                    <td style={{ color: '#888', fontSize: '0.85rem' }}>
                      {file.uploaded_by || '—'}
                    </td>
                    <td>{formatDateTime(file.last_modified)}</td>
                    <td>
                      <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap' }}>
                        <button
                          className="button-primary"
                          style={{ fontSize: '0.8rem', padding: '0.3rem 0.8rem' }}
                          onClick={() => handleDownload(file.key)}
                        >
                          Download
                        </button>
                        {bucketAPI.isLikelyNexusFile(file.key) && (
                          <Link
                            to={`/buckets/${id}/nexus?file=${bucketAPI.encodeFileKey(file.key)}`}
                            className="button-secondary"
                            style={{ fontSize: '0.8rem', padding: '0.3rem 0.8rem' }}
                          >
                            View
                          </Link>
                        )}
                        {!bucketAPI.isLikelyNexusFile(file.key) && bucketAPI.isViewableFile(file.key) && (
                          <button
                            className="button-secondary"
                            style={{ fontSize: '0.8rem', padding: '0.3rem 0.8rem' }}
                            onClick={() => setViewingFile({ key: file.key, size: file.size })}
                          >
                            View
                          </button>
                        )}
                        {canUpload && (
                          <button
                            className="button-delete-small"
                            onClick={() => handleDeleteFile(file.key)}
                          >
                            Delete
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="file-count" style={{ padding: '0.5rem 1rem' }}>
              {bucket.files.length} file{bucket.files.length !== 1 ? 's' : ''}
            </p>
          </div>
        )}
      </div>

      {showShareModal && bucket && (
        <ShareModal
          bucketId={bucket.id}
          bucketName={bucket.display_name || bucket.name}
          onClose={() => setShowShareModal(false)}
        />
      )}

      {viewingFile && bucket && (
        <FileViewer
          bucketId={bucket.id}
          fileKey={viewingFile.key}
          fileSize={viewingFile.size}
          onClose={() => setViewingFile(null)}
        />
      )}
    </div>
  )
}

export default BucketDetail
