import { useAutoError, useAutoSuccess } from '../../hooks/useAutoMessage'
import { useState, useEffect } from 'react'
import { adminAPI, getApiError } from '../../services/api'
import MarkdownViewer from '../MarkdownViewer'
import type { TenantDocument, AdminAvailableTenant } from '../../types'

function TenantDocView() {
  const [tenants, setTenants] = useState<AdminAvailableTenant[]>([])
  const [selectedTenant, setSelectedTenant] = useState('')
  const [doc, setDoc] = useState<TenantDocument | null>(null)
  const [hasDoc, setHasDoc] = useState(false)
  const [loading, setLoading] = useState(true)
  const [loadingDoc, setLoadingDoc] = useState(false)
  const [saving, setSaving] = useState(false)
  const [removing, setRemoving] = useState(false)
  const [error, setError] = useAutoError()
  const [success, setSuccess] = useAutoSuccess()

  // Draft state
  const [draftContent, setDraftContent] = useState('')
  const [draftTabName, setDraftTabName] = useState('Documentation')
  const [draftVisible, setDraftVisible] = useState(false)
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [showPreview, setShowPreview] = useState(false)

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
    setLoadingDoc(true)
    setError(null)
    setSuccess(null)
    setSelectedFile(null)
    adminAPI.getTenantDocument(selectedTenant)
      .then(d => {
        setDoc(d)
        setHasDoc(true)
        setDraftContent(d.content)
        setDraftTabName(d.tab_name)
        setDraftVisible(d.is_visible)
      })
      .catch(err => {
        if ((err as {response?: {status?: number}})?.response?.status === 404) {
          setDoc(null)
          setHasDoc(false)
          setDraftContent('')
          setDraftTabName('Documentation')
          setDraftVisible(false)
        } else {
          setError(getApiError(err, 'Failed to load document'))
        }
      })
      .finally(() => setLoadingDoc(false))
  }, [selectedTenant])

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0] || null
    setSelectedFile(file)
    if (file) {
      file.text().then(text => {
        setDraftContent(text)
        setSuccess(null)
      })
    }
  }

  const handleSave = async () => {
    if (!selectedTenant) return
    setSaving(true)
    setError(null)
    setSuccess(null)
    try {
      const fd = new FormData()
      fd.append('tab_name', draftTabName)
      fd.append('is_visible', String(draftVisible))
      if (selectedFile) {
        fd.append('file', selectedFile)
      } else {
        fd.append('content', draftContent)
      }
      const saved = await adminAPI.saveTenantDocument(selectedTenant, fd)
      setDoc(saved)
      setHasDoc(true)
      setDraftContent(saved.content)
      setDraftTabName(saved.tab_name)
      setDraftVisible(saved.is_visible)
      setSelectedFile(null)
      setSuccess('Document saved.')
    } catch (err: unknown) {
      setError(getApiError(err, 'Failed to save document'))
    } finally {
      setSaving(false)
    }
  }

  const handleRemove = async () => {
    if (!selectedTenant || !hasDoc) return
    setRemoving(true)
    setError(null)
    setSuccess(null)
    try {
      await adminAPI.deleteTenantDocument(selectedTenant)
      setDoc(null)
      setHasDoc(false)
      setDraftContent('')
      setDraftTabName('Documentation')
      setDraftVisible(false)
      setSelectedFile(null)
      setSuccess('Document removed. The nav tab is no longer visible to users.')
    } catch (err: unknown) {
      setError(getApiError(err, 'Failed to remove document'))
    } finally {
      setRemoving(false)
    }
  }

  if (loading) return <div className="admin-loading">Loading…</div>

  return (
    <div>
      <div className="admin-page-header">
        <h1>Tenant Documentation</h1>
        <select
          value={selectedTenant}
          onChange={e => setSelectedTenant(e.target.value)}
          className="admin-select"
        >
          <option value="">Select tenant…</option>
          {tenants.map(t => <option key={t.structure} value={t.structure}>{t.structure}</option>)}
        </select>
      </div>

      <p style={{ color: '#6b7280', marginBottom: '1rem', maxWidth: '640px' }}>
        Upload or write a Markdown document for users of this tenant. Set a custom tab name and
        toggle visibility. The tab only appears in the user's nav when the document is non-empty
        and visibility is enabled.
      </p>

      {error && <div className="error-message" onClick={() => setError(null)}>{error}</div>}
      {success && <div className="success-message" onClick={() => setSuccess(null)}>{success}</div>}

      {loadingDoc ? (
        <div className="admin-loading">Loading document…</div>
      ) : selectedTenant ? (
        <div style={{ display: 'flex', gap: '1.5rem', alignItems: 'flex-start', flexWrap: 'wrap' }}>

          {/* Left: editor panel */}
          <div style={{ flex: '1 1 380px', minWidth: '320px' }}>
            {hasDoc && doc && (
              <div style={{
                padding: '0.5rem 0.75rem', borderRadius: '6px', marginBottom: '1rem',
                background: doc.is_visible && doc.content ? '#f0fdf4' : '#f8fafc',
                border: `1px solid ${doc.is_visible && doc.content ? '#bbf7d0' : '#e2e8f0'}`,
                color: doc.is_visible && doc.content ? '#166534' : '#64748b',
                fontSize: '0.85rem', display: 'flex', alignItems: 'center', gap: '0.5rem',
              }}>
                <span>{doc.is_visible && doc.content ? '●' : '○'}</span>
                {doc.is_visible && doc.content
                  ? `Visible to users as "${doc.tab_name}"`
                  : doc.content
                    ? 'Document hidden (toggle to show)'
                    : 'No content — tab will not appear to users'}
                {doc.updated_at && (
                  <span style={{ marginLeft: 'auto', color: '#94a3b8' }}>
                    Updated {new Date(doc.updated_at).toLocaleDateString()}
                  </span>
                )}
              </div>
            )}

            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
              <label>
                <span style={{ fontWeight: 500, display: 'block', marginBottom: '0.3rem' }}>Tab Name</span>
                <input
                  type="text"
                  value={draftTabName}
                  onChange={e => setDraftTabName(e.target.value)}
                  placeholder="e.g. Tutorial, Documentation, Guide"
                  className="input-field"
                  style={{ width: '100%' }}
                />
              </label>

              <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={draftVisible}
                  onChange={e => setDraftVisible(e.target.checked)}
                />
                <span style={{ fontWeight: 500 }}>Visible to users</span>
                <span style={{ color: '#94a3b8', fontSize: '0.8rem' }}>
                  (requires non-empty content)
                </span>
              </label>

              <label>
                <span style={{ fontWeight: 500, display: 'block', marginBottom: '0.3rem' }}>
                  Upload Markdown file <span style={{ fontWeight: 400, color: '#94a3b8' }}>(replaces content below)</span>
                </span>
                <input
                  type="file"
                  accept=".md,.markdown,.txt"
                  onChange={handleFileChange}
                />
                {selectedFile && (
                  <span style={{ fontSize: '0.8rem', color: '#2563eb', marginTop: '0.25rem', display: 'block' }}>
                    {selectedFile.name} loaded — content updated in editor below
                  </span>
                )}
              </label>

              <label>
                <span style={{ fontWeight: 500, display: 'block', marginBottom: '0.3rem' }}>
                  Content <span style={{ fontWeight: 400, color: '#94a3b8' }}>(edit directly or upload file above)</span>
                </span>
                <textarea
                  value={draftContent}
                  onChange={e => { setDraftContent(e.target.value); setSelectedFile(null) }}
                  placeholder="# Your Markdown here&#10;&#10;Write content in Markdown..."
                  rows={16}
                  autoComplete="off"
                  data-lpignore="true"
                  style={{
                    width: '100%', fontFamily: 'ui-monospace, SFMono-Regular, monospace',
                    fontSize: '0.85rem', padding: '0.6rem', borderRadius: '6px',
                    border: '1px solid #cbd5e1', resize: 'vertical', lineHeight: 1.6,
                    background: '#f8fafc',
                  }}
                />
              </label>
            </div>

            {/* Primary actions */}
            <div style={{ display: 'flex', gap: '0.5rem', marginTop: '1rem' }}>
              <button
                type="button"
                className="button-primary"
                onClick={handleSave}
                disabled={saving || !selectedTenant}
              >
                {saving ? 'Saving…' : 'Save'}
              </button>
              <button
                type="button"
                className="button-secondary"
                onClick={() => setShowPreview(p => !p)}
              >
                {showPreview ? 'Hide Preview' : 'Show Preview'}
              </button>
            </div>

            {/* Destructive zone — visually separated, full-width for clarity */}
            {hasDoc && (
              <div style={{ marginTop: '1rem', paddingTop: '1rem', borderTop: '2px solid #fee2e2' }}>
                <button
                  type="button"
                  data-form-type="other"
                  onClick={handleRemove}
                  disabled={removing}
                  style={{
                    background: removing ? '#9ca3af' : '#dc2626',
                    color: 'white', border: 'none',
                    padding: '0.6rem 1.25rem', borderRadius: '6px',
                    cursor: removing ? 'not-allowed' : 'pointer',
                    fontSize: '0.875rem', fontWeight: 600, display: 'block',
                    marginBottom: '0.25rem',
                  }}
                >
                  {removing ? 'Removing…' : '🗑 Remove Document'}
                </button>
                <div style={{ fontSize: '0.78rem', color: '#9ca3af' }}>
                  Clears content and hides the tab from users.
                </div>
              </div>
            )}
          </div>

          {/* Right: live preview */}
          {showPreview && (
            <div style={{
              flex: '1 1 400px', minWidth: '320px',
              border: '1px solid #e2e8f0', borderRadius: '8px',
              padding: '1rem 1.25rem', background: '#fff',
              maxHeight: '80vh', overflowY: 'auto',
            }}>
              <div style={{
                fontSize: '0.75rem', color: '#94a3b8', marginBottom: '0.75rem',
                borderBottom: '1px solid #f1f5f9', paddingBottom: '0.4rem',
              }}>
                Preview — {draftTabName || 'Untitled'}
              </div>
              {draftContent.trim() ? (
                <MarkdownViewer content={draftContent} />
              ) : (
                <div style={{ color: '#94a3b8', fontStyle: 'italic' }}>Nothing to preview yet.</div>
              )}
            </div>
          )}
        </div>
      ) : (
        <div className="admin-loading">Select a tenant to manage its documentation.</div>
      )}
    </div>
  )
}

export default TenantDocView
