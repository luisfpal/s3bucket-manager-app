import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'

import '@h5web/app/styles.css'
import { App as H5WebApp } from '@h5web/app'
import { H5WasmBufferProvider } from '@h5web/h5wasm'

import Navbar from './Navbar'
import { authAPI, bucketAPI } from '../services/api'
import { formatSize } from '../utils/format'
import type { NexusDetectResponse, User } from '../types'

const BROWSER_BUFFER_WARNING_BYTES = 700 * 1024 * 1024
const LARGE_LOADER_RADIUS = 46
const LARGE_LOADER_CIRCUMFERENCE = 2 * Math.PI * LARGE_LOADER_RADIUS

function getErrorMessage(err: unknown, fallback: string): string {
  return (err as { response?: { data?: { error?: string } } })?.response?.data?.error || fallback
}

function NexusViewer() {
  const { id } = useParams<{ id: string }>()
  const [searchParams] = useSearchParams()

  const encodedFileKey = searchParams.get('file')
  const fileKey = useMemo(() => {
    if (!encodedFileKey) return ''
    try {
      return decodeURIComponent(encodedFileKey)
    } catch {
      return encodedFileKey
    }
  }, [encodedFileKey])

  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [fileSize, setFileSize] = useState<number>(0)
  const [, setDetect] = useState<NexusDetectResponse | null>(null)

  const [h5Buffer, setH5Buffer] = useState<ArrayBuffer | null>(null)
  const [downloadPercent, setDownloadPercent] = useState<number | null>(null)
  const immersiveControllerRef = useRef<AbortController | null>(null)

  const bucketId = useMemo(() => (id ? parseInt(id) : null), [id])

  const fileName = useMemo(() => {
    if (!fileKey) return 'nexus-file.nxs'
    const candidate = fileKey.split('/').pop()
    return candidate && candidate.trim() ? candidate : 'nexus-file.nxs'
  }, [fileKey])

  const showLargeFileWarning = fileSize > BROWSER_BUFFER_WARNING_BYTES

  useEffect(() => {
    let isActive = true
    const controller = new AbortController()
    immersiveControllerRef.current = controller

    const loadImmersiveViewer = async () => {
      let detectedSize = 0

      if (!bucketId || !fileKey) {
        setError('Missing bucket id or file key in URL.')
        setLoading(false)
        return
      }

      try {
        setLoading(true)
        setError(null)
        setH5Buffer(null)
        setDownloadPercent(null)

        const userData = await authAPI.getCurrentUser()
        if (!isActive) return
        setUser(userData)

        const detectData = await bucketAPI.nexusDetect(bucketId, fileKey)
        if (!isActive) return
        setDetect(detectData)
        setFileSize(detectData.size)
        detectedSize = detectData.size

        if (!detectData.is_nexus) {
          setError('Selected file is not a valid NeXus/HDF5 file.')
          setLoading(false)
          return
        }

        const blob = await bucketAPI.getFileBlob(bucketId, fileKey, {
          signal: controller.signal,
          onProgress: (percent) => {
            if (!isActive) return
            setDownloadPercent(percent)
          },
        })

        if (!isActive) return
        const arrayBuffer = await blob.arrayBuffer()
        if (!isActive) return

        setH5Buffer(arrayBuffer)
      } catch (err: unknown) {
        if (!isActive) return
        const maybeError = err as { name?: string; response?: { data?: { error?: string } } }
        if (maybeError?.name === 'CanceledError' || maybeError?.name === 'AbortError') {
          setError(null)
          return
        }

        const backendError = getErrorMessage(err, 'Failed to open this file.')
        const largeFileHint = detectedSize > BROWSER_BUFFER_WARNING_BYTES
          ? ' This file is large; close other heavy tabs and try again.'
          : ''
        setError(`${backendError}${largeFileHint}`)
      } finally {
        if (!isActive) return
        if (immersiveControllerRef.current === controller) {
          immersiveControllerRef.current = null
        }
        setLoading(false)
      }
    }

    loadImmersiveViewer()

    return () => {
      isActive = false
      controller.abort()
      if (immersiveControllerRef.current === controller) {
        immersiveControllerRef.current = null
      }
    }
  }, [bucketId, fileKey])

  const circularProgress = Math.max(0, Math.min(100, downloadPercent ?? 0))
  const circularProgressOffset = LARGE_LOADER_CIRCUMFERENCE - (circularProgress / 100) * LARGE_LOADER_CIRCUMFERENCE

  const canDownload = Boolean(fileKey && bucketId)

  const loadingTitle = 'Opening NeXus file...'
  const loadingSubtitle = 'Please wait while the viewer prepares your data.'

  const emptyMessage = error || 'Viewer is not ready yet.'

  const isReady = !loading && Boolean(h5Buffer)
  const showCircularLoader = loading && fileSize > 0

  const h5ProviderKey = `${id}:${fileKey}`

  const openDownload = () => {
    if (!bucketId || !fileKey) return
    void bucketAPI.downloadFile(bucketId, fileKey)
  }

  return (
    <div className="page-container">
      <Navbar user={user} />

      <div className="nexus-viewer-container">
        <div className="nexus-viewer-header">
          <div className="nexus-header-main">
            <Link to={`/buckets/${id}`} className="breadcrumb">&larr; Back to Bucket</Link>
            <h1>NeXus Viewer</h1>
            <p className="nexus-file-caption">
              {fileName}
              {fileSize > 0 && ` · ${formatSize(fileSize)}`}
            </p>
          </div>
          <div className="nexus-header-actions">
            {canDownload && (
              <button className="button-primary nexus-action-button" onClick={openDownload}>
                Download
              </button>
            )}
          </div>
        </div>

        {showCircularLoader && (
          <div className="nexus-circular-loader-card" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={circularProgress}>
            <div className="nexus-circular-loader-wrap">
              <svg className="nexus-circular-loader" viewBox="0 0 120 120" aria-hidden="true">
                <circle className="nexus-circular-track" cx="60" cy="60" r={LARGE_LOADER_RADIUS} />
                <circle
                  className="nexus-circular-progress"
                  cx="60"
                  cy="60"
                  r={LARGE_LOADER_RADIUS}
                  strokeDasharray={LARGE_LOADER_CIRCUMFERENCE}
                  strokeDashoffset={circularProgressOffset}
                />
              </svg>
              <span className="nexus-circular-label">{Math.round(circularProgress)}%</span>
            </div>
          </div>
        )}

        {showLargeFileWarning && !loading && (
          <div className="nexus-warning-banner">
            Large file detected. Opening may take longer and use more browser memory.
          </div>
        )}

        {error && (
          <div className="error-message nexus-error-card">
            <div>{error}</div>
            {canDownload && (
              <div className="nexus-error-actions">
                <button className="button-primary nexus-action-button" onClick={openDownload}>Download</button>
              </div>
            )}
          </div>
        )}

        {loading && !h5Buffer && !showCircularLoader ? (
          <div className="loading-container nexus-loading-card">
            <h2>{loadingTitle}</h2>
            <p>{loadingSubtitle}</p>
          </div>
        ) : isReady ? (
          <div className="nexus-h5web-shell">
            <H5WasmBufferProvider
              key={h5ProviderKey}
              filename={fileName}
              buffer={h5Buffer!}
            >
              <div className="nexus-h5web-canvas">
                <H5WebApp sidebarOpen disableDarkMode />
              </div>
            </H5WasmBufferProvider>
          </div>
        ) : (
          <div className="error-container nexus-empty-card">
            <h2>Viewer unavailable</h2>
            <p>{emptyMessage}</p>
            {canDownload && (
              <div className="nexus-error-actions">
                <button className="button-primary nexus-action-button" onClick={openDownload}>Download</button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

export default NexusViewer
