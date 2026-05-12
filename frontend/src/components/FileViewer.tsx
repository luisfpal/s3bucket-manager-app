/** Modal file viewer with inline renderers for supported file types. */

import { useState, useEffect, useRef } from 'react'
import { bucketAPI, getApiError } from '../services/api'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

const MAX_TEXT_BYTES = 5 * 1024 * 1024 // 5 MB

type ViewerType = 'image' | 'text' | 'pdf' | 'csv' | 'html' | 'markdown'

interface FileViewerProps {
  bucketId: number
  fileKey: string
  fileSize: number
  onClose: () => void
}

function getViewerType(fileKey: string): ViewerType {
  return bucketAPI.isViewableFile(fileKey) || 'text'
}

function getFileName(fileKey: string): string {
  return fileKey.split('/').pop() || fileKey
}

/**
 * Parse CSV text into rows of cells, handling quoted fields with commas.
 */
function parseCSV(text: string): string[][] {
  const rows: string[][] = []
  let current = ''
  let inQuotes = false
  let row: string[] = []

  for (let i = 0; i < text.length; i++) {
    const ch = text[i]
    if (inQuotes) {
      if (ch === '"' && text[i + 1] === '"') {
        current += '"'
        i++ // skip escaped quote
      } else if (ch === '"') {
        inQuotes = false
      } else {
        current += ch
      }
    } else {
      if (ch === '"') {
        inQuotes = true
      } else if (ch === ',') {
        row.push(current.trim())
        current = ''
      } else if (ch === '\n' || (ch === '\r' && text[i + 1] === '\n')) {
        row.push(current.trim())
        if (row.some(cell => cell !== '')) rows.push(row)
        row = []
        current = ''
        if (ch === '\r') i++ // skip \n after \r
      } else {
        current += ch
      }
    }
  }
  if (current || row.length > 0) {
    row.push(current.trim())
    if (row.some(cell => cell !== '')) rows.push(row)
  }
  return rows
}

function FileViewer({ bucketId, fileKey, fileSize, onClose }: FileViewerProps) {
  const viewerType = getViewerType(fileKey)
  const fileName = getFileName(fileKey)
  const isTextBased = viewerType === 'text' || viewerType === 'csv' || viewerType === 'html' || viewerType === 'markdown'

  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [objectUrl, setObjectUrl] = useState<string | null>(null)
  const [textContent, setTextContent] = useState<string | null>(null)
  const [imageZoomed, setImageZoomed] = useState(false)
  const overlayRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let isActive = true
    const controller = new AbortController()

    const loadFile = async () => {
      setLoading(true)
      setError(null)

      // Text previews are bounded so large objects do not freeze the browser.
      if (isTextBased && fileSize > MAX_TEXT_BYTES) {
        setError(`File is too large to preview (${(fileSize / 1024 / 1024).toFixed(1)} MB). Maximum: 5 MB.`)
        setLoading(false)
        return
      }

      try {
        const blob = await bucketAPI.getFileBlob(bucketId, fileKey, { signal: controller.signal })
        if (!isActive) return

        if (isTextBased) {
          const text = await blob.text()
          if (!isActive) return

          const ext = fileKey.toLowerCase().split('.').pop()
          if (ext === 'json') {
            try {
              setTextContent(JSON.stringify(JSON.parse(text), null, 2))
            } catch {
              setTextContent(text)
            }
          } else {
            setTextContent(text)
          }
        } else {
          const url = URL.createObjectURL(blob)
          if (!isActive) {
            URL.revokeObjectURL(url)
            return
          }
          setObjectUrl(url)
        }
      } catch (err: unknown) {
        if (!isActive) return
        const maybeError = err as { name?: string }
        if (maybeError?.name === 'CanceledError' || maybeError?.name === 'AbortError') return
        setError(getApiError(err, 'Failed to load file'))
      } finally {
        if (isActive) setLoading(false)
      }
    }

    loadFile()

    return () => {
      isActive = false
      controller.abort()
    }
  }, [bucketId, fileKey, fileSize, viewerType, isTextBased])

  // Browser object URLs pin memory until they are revoked.
  useEffect(() => {
    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [objectUrl])

  const handleDownload = async () => {
    try {
      await bucketAPI.downloadFile(bucketId, fileKey)
    } catch { /* ignore */ }
  }

  const handleOverlayClick = (e: React.MouseEvent) => {
    if (e.target === overlayRef.current) onClose()
  }

  const csvRows = viewerType === 'csv' && textContent ? parseCSV(textContent) : []
  const csvHeader = csvRows.length > 0 ? csvRows[0] : []
  const csvBody = csvRows.length > 1 ? csvRows.slice(1) : []

  const viewerLabel = viewerType === 'csv'
    ? 'CSV'
    : viewerType === 'html'
      ? 'HTML'
      : viewerType === 'markdown'
        ? 'Markdown'
        : viewerType.toUpperCase()
  const modalWidth = viewerType === 'pdf' || viewerType === 'html' || viewerType === 'csv' || viewerType === 'markdown' ? '90vw' : '80vw'

  return (
    <div
      ref={overlayRef}
      onClick={handleOverlayClick}
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        background: 'rgba(0, 0, 0, 0.7)',
        zIndex: 1000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      <div style={{
        background: '#fff',
        borderRadius: '12px',
        maxWidth: modalWidth,
        maxHeight: '90vh',
        width: viewerType === 'pdf' || viewerType === 'html' ? '90vw' : undefined,
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
        boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.25)',
      }}>
        <div style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          padding: '0.75rem 1rem',
          borderBottom: '1px solid #e2e8f0',
          background: '#f8fafc',
          flexShrink: 0,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', minWidth: 0 }}>
            <span style={{ fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {fileName}
            </span>
            <span style={{ fontSize: '0.75rem', color: '#94a3b8', flexShrink: 0 }}>
              {viewerLabel}
            </span>
          </div>
          <div style={{ display: 'flex', gap: '0.5rem', flexShrink: 0 }}>
            <button
              onClick={handleDownload}
              className="button-secondary"
              style={{ fontSize: '0.8rem', padding: '0.3rem 0.8rem' }}
            >
              Download
            </button>
            <button
              onClick={onClose}
              style={{
                background: 'none',
                border: '1px solid #e2e8f0',
                borderRadius: '6px',
                padding: '0.3rem 0.6rem',
                cursor: 'pointer',
                fontSize: '0.9rem',
                color: '#64748b',
              }}
            >
              ✕
            </button>
          </div>
        </div>

        <div style={{
          flex: 1,
          overflow: 'auto',
          display: 'flex',
          alignItems: viewerType === 'csv' ? 'flex-start' : 'center',
          justifyContent: 'center',
          minHeight: '200px',
        }}>
          {loading && (
            <div style={{ textAlign: 'center', padding: '2rem', color: '#64748b' }}>
              <div style={{
                width: '40px',
                height: '40px',
                border: '3px solid #e2e8f0',
                borderTop: '3px solid #3b82f6',
                borderRadius: '50%',
                animation: 'spin 1s linear infinite',
                margin: '0 auto 1rem',
              }} />
              Loading file...
              <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
            </div>
          )}

          {error && (
            <div style={{ textAlign: 'center', padding: '2rem' }}>
              <p style={{ color: '#dc2626', marginBottom: '1rem' }}>{error}</p>
              <button onClick={handleDownload} className="button-primary">
                Download Instead
              </button>
            </div>
          )}

          {!loading && !error && viewerType === 'image' && objectUrl && (
            <img
              src={objectUrl}
              alt={fileName}
              onClick={() => setImageZoomed(!imageZoomed)}
              style={{
                maxWidth: imageZoomed ? 'none' : '100%',
                maxHeight: imageZoomed ? 'none' : '80vh',
                objectFit: 'contain',
                cursor: imageZoomed ? 'zoom-out' : 'zoom-in',
                display: 'block',
              }}
            />
          )}

          {!loading && !error && viewerType === 'text' && textContent !== null && (
            <pre style={{
              width: '100%',
              margin: 0,
              padding: '1rem',
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
              fontSize: '0.85rem',
              lineHeight: 1.6,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              overflow: 'auto',
              maxHeight: '80vh',
              background: '#1e293b',
              color: '#e2e8f0',
            }}>
              {textContent}
            </pre>
          )}

          {!loading && !error && viewerType === 'csv' && textContent !== null && (
            <div style={{ width: '100%', overflow: 'auto', maxHeight: '80vh' }}>
              <table style={{
                borderCollapse: 'collapse',
                width: '100%',
                fontSize: '0.85rem',
                fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
              }}>
                {csvHeader.length > 0 && (
                  <thead>
                    <tr>
                      {csvHeader.map((cell, i) => (
                        <th key={i} style={{
                          padding: '0.5rem 0.75rem',
                          background: '#1e293b',
                          color: '#e2e8f0',
                          fontWeight: 600,
                          textAlign: 'left',
                          borderBottom: '2px solid #334155',
                          whiteSpace: 'nowrap',
                          position: 'sticky',
                          top: 0,
                        }}>
                          {cell}
                        </th>
                      ))}
                    </tr>
                  </thead>
                )}
                <tbody>
                  {csvBody.map((row, ri) => (
                    <tr key={ri} style={{ background: ri % 2 === 0 ? '#fff' : '#f8fafc' }}>
                      {row.map((cell, ci) => (
                        <td key={ci} style={{
                          padding: '0.4rem 0.75rem',
                          borderBottom: '1px solid #e2e8f0',
                          whiteSpace: 'nowrap',
                        }}>
                          {cell}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
              {csvBody.length === 0 && (
                <p style={{ padding: '1rem', color: '#94a3b8', textAlign: 'center' }}>No data rows</p>
              )}
            </div>
          )}

          {!loading && !error && viewerType === 'html' && textContent !== null && (
            <iframe
              srcDoc={textContent}
              sandbox="allow-same-origin"
              title={fileName}
              style={{
                width: '100%',
                height: '80vh',
                border: 'none',
                background: '#fff',
              }}
            />
          )}

          {!loading && !error && viewerType === 'markdown' && textContent !== null && (
            <div style={{
              width: '100%',
              maxHeight: '80vh',
              overflow: 'auto',
              padding: '1rem 1.25rem',
              lineHeight: 1.6,
              color: '#0f172a',
            }}>
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  pre: ({ node, ...props }) => (
                    <pre
                      {...props}
                      style={{
                        background: '#0f172a',
                        color: '#e2e8f0',
                        padding: '0.8rem',
                        borderRadius: '8px',
                        overflowX: 'auto',
                      }}
                    />
                  ),
                }}
              >
                {textContent}
              </ReactMarkdown>
            </div>
          )}

          {!loading && !error && viewerType === 'pdf' && objectUrl && (
            <iframe
              src={objectUrl}
              title={fileName}
              style={{
                width: '100%',
                height: '80vh',
                border: 'none',
              }}
            />
          )}
        </div>
      </div>
    </div>
  )
}

export default FileViewer
