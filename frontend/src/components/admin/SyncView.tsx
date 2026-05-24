import { useAutoError } from '../../hooks/useAutoMessage'
/**
 * SyncView — NFFADI instruments CSV upload.
 *
 * Uploads the CSV to RGWSquared (/s3structnffadi/csvUpload), which stores the
 * instrument list for its scheduled 4-hour structure update cycle.
 * UO codes and institution mappings are updated immediately from the CSV.
 *
 * Tenant refresh (pulling users/buckets from RGWSquared into the local DB) is
 * handled by the per-tenant "Refresh" buttons in the Tenants view, not here.
 */

import { useState, useRef } from 'react'
import { adminAPI, getApiError } from '../../services/api'

interface UploadResult {
  instruments_uploaded?: number
  uo_codes_updated?: number
  execTime?: string
  [key: string]: unknown
}

function SyncView() {
  const [uploading, setUploading] = useState(false)
  const [result, setResult] = useState<UploadResult | null>(null)
  const [error, setError] = useAutoError()
  const fileRef = useRef<HTMLInputElement>(null)

  const handleUpload = async (e: React.FormEvent) => {
    e.preventDefault()
    const file = fileRef.current?.files?.[0]
    if (!file) {
      setError('Please select a CSV file.')
      return
    }
    if (file.size > 10 * 1024 * 1024) {
      setError('File too large (max 10 MB).')
      return
    }
    if (!file.name.toLowerCase().endsWith('.csv')) {
      setError('Please select a .csv file.')
      return
    }

    setUploading(true)
    setError(null)
    setResult(null)

    try {
      const res = await adminAPI.syncUploadCSV(file) as UploadResult
      setResult(res)
      if (fileRef.current) fileRef.current.value = ''
    } catch (err: unknown) {
      setError(getApiError(err, 'CSV upload failed'))
    } finally {
      setUploading(false)
    }
  }

  return (
    <div>
      <div className="admin-page-header">
        <h1>NFFADI CSV Upload</h1>
      </div>

      <p style={{ color: '#6b7280', marginBottom: '1rem', maxWidth: '640px' }}>
        Upload the NFFADI instruments CSV to RGWSquared. UO codes for users will be
        updated immediately from the CSV. RGWSquared will process the instrument list
        on its scheduled 4-hour cycle.
      </p>
      <p style={{ color: '#94a3b8', marginBottom: '1.5rem', maxWidth: '640px', fontSize: '0.8rem' }}>
        To pull the latest users and buckets from RGWSquared into the local database,
        use the <strong>Refresh</strong> button on the <a href="/admin/tenants" style={{ color: '#6366f1' }}>Tenants page</a>.
      </p>

      {error && (
        <div className="error-message" style={{ marginBottom: '1rem' }} onClick={() => setError(null)}>
          {error}
        </div>
      )}

      {result && (
        <div style={{
          marginBottom: '1.5rem', padding: '0.75rem 1rem',
          background: '#f0fdf4', border: '1px solid #bbf7d0', borderRadius: '6px',
          color: '#166534', fontSize: '0.875rem',
        }}>
          <div style={{ fontWeight: 600, marginBottom: '0.25rem' }}>✓ CSV uploaded successfully</div>
          {result.instruments_uploaded !== undefined && (
            <div>Instruments uploaded: <strong>{result.instruments_uploaded}</strong></div>
          )}
          {result.uo_codes_updated !== undefined && (
            <div>User UO codes updated: <strong>{result.uo_codes_updated}</strong></div>
          )}
          {result.execTime && (
            <div style={{ color: '#6b7280', marginTop: '0.25rem', fontSize: '0.8rem' }}>
              {String(result.execTime)}
            </div>
          )}
        </div>
      )}

      <div className="admin-sync-section">
        <form onSubmit={handleUpload}>
          <div style={{ display: 'flex', gap: '1rem', alignItems: 'center', flexWrap: 'wrap' }}>
            <input type="file" accept=".csv" ref={fileRef} disabled={uploading} />
            <button type="submit" className="button-primary" disabled={uploading}>
              {uploading ? 'Uploading…' : 'Upload CSV'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

export default SyncView
