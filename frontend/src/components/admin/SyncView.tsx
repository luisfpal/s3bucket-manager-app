import { useState, useEffect, useRef } from 'react'
import { adminAPI, getApiError } from '../../services/api'
import type { AdminAvailableTenant } from '../../types'

type StepStatus = 'pending' | 'running' | 'done' | 'failed' | 'skipped'

interface PipelineStep {
  label: string
  status: StepStatus
  result?: string
  error?: string
}

const INITIAL_STEPS: PipelineStep[] = [
  { label: '1. Upload CSV to RGWSquared', status: 'pending' },
  { label: '2. Fetch proposals from external API', status: 'pending' },
  { label: '3. Generate structure definition', status: 'pending' },
  { label: '4. Apply structure to Ceph RGW', status: 'pending' },
  { label: '5. Refresh local Django cache', status: 'pending' },
]

function statusIcon(s: StepStatus) {
  switch (s) {
    case 'pending': return '\u23f3'
    case 'running': return '\ud83d\udd04'
    case 'done': return '\u2705'
    case 'failed': return '\u274c'
    case 'skipped': return '\u23ed\ufe0f'
  }
}

function SyncView() {
  const [structures, setStructures] = useState<AdminAvailableTenant[]>([])
  const [selectedStructure, setSelectedStructure] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<string | null>(null)

  const [syncing, setSyncing] = useState(false)
  const [steps, setSteps] = useState<PipelineStep[]>(INITIAL_STEPS)
  const [pipelineRunning, setPipelineRunning] = useState(false)
  const [skipProposals, setSkipProposals] = useState(true)
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    (async () => {
      try {
        const data = await adminAPI.getAvailableTenants()
        setStructures(data)
        if (data.length > 0) setSelectedStructure(data[0].structure)
      } catch (err: unknown) {
        setError(getApiError(err, 'Failed to load structures from RGWSquared'))
      } finally {
        setLoading(false)
      }
    })()
  }, [])

  const handleRefresh = async () => {
    if (!selectedStructure) return
    try {
      setSyncing(true)
      setError(null)
      setResult(null)
      const data = await adminAPI.syncRefresh(selectedStructure)
      setResult(JSON.stringify(data, null, 2))
    } catch (err: unknown) {
      setError(getApiError(err, 'Sync refresh failed'))
    } finally {
      setSyncing(false)
    }
  }

  const updateStep = (idx: number, updates: Partial<PipelineStep>) => {
    setSteps(prev => prev.map((s, i) => i === idx ? { ...s, ...updates } : s))
  }

  const runPipeline = async () => {
    const file = fileRef.current?.files?.[0]
    if (!file) {
      setError('Select a CSV file first')
      return
    }
    if (file.size > 10 * 1024 * 1024) {
      setError('File too large (max 10 MB)')
      return
    }
    const structure = selectedStructure || 'NFFADI'

    setPipelineRunning(true)
    setError(null)
    setResult(null)
    setSteps(INITIAL_STEPS.map((s, i) =>
      (i === 1 && skipProposals) ? { ...s, status: 'skipped' as StepStatus } : s
    ))

    try {
      // Keep sync explicit so operators can recover at the RGWSquared/Ceph boundary that failed.
      updateStep(0, { status: 'running' })
      const csvResult = await adminAPI.syncUploadCSV(file)
      updateStep(0, { status: 'done', result: summarize(csvResult) })

      if (skipProposals) {
        updateStep(1, { status: 'skipped', result: 'Skipped — using existing proposals data in CouchDB' })
      } else {
        updateStep(1, { status: 'running' })
        try {
          const propResult = await adminAPI.syncProposals()
          updateStep(1, { status: 'done', result: summarize(propResult) })
        } catch (err: unknown) {
          const msg = getApiError(err, 'Proposals fetch failed')
          updateStep(1, { status: 'failed', error: msg })
          // Existing CouchDB proposals are enough for the next RGWSquared step.
        }
      }

      updateStep(2, { status: 'running' })
      const genResult = await adminAPI.syncGenerate(structure)
      updateStep(2, { status: 'done', result: summarize(genResult) })

      updateStep(3, { status: 'running' })
      const applyResult = await adminAPI.syncApply(structure)
      const applyMsg = applyResult.status === 'in_progress'
        ? 'Ceph sync continues server-side (timeout expected)'
        : summarize(applyResult)
      updateStep(3, { status: 'done', result: applyMsg })

      updateStep(4, { status: 'running' })
      const refreshResult = await adminAPI.syncRefresh(structure)
      updateStep(4, { status: 'done', result: summarize(refreshResult) })

      setResult('Pipeline completed successfully')
      if (fileRef.current) fileRef.current.value = ''
    } catch (err: unknown) {
      const msg = getApiError(err, 'Pipeline step failed')
      setError(msg)
      setSteps(prev => prev.map(s => s.status === 'running' ? { ...s, status: 'failed', error: msg } : s))
    } finally {
      setPipelineRunning(false)
    }
  }

  if (loading) return <div className="admin-loading">Loading...</div>

  return (
    <div>
      <div className="admin-page-header">
        <h1>Sync Operations</h1>
      </div>

      {error && <div className="error-message" onClick={() => setError(null)}>{error}</div>}
      {result && !pipelineRunning && (
        <div className="success-message">
          <pre style={{ whiteSpace: 'pre-wrap', fontSize: '0.8rem' }}>{result}</pre>
        </div>
      )}

      <div className="admin-sync-section">
        <h2>Refresh Local Cache</h2>
        <p>Sync RGWSquared state into Django database for a specific tenant.</p>
        <div style={{ display: 'flex', gap: '0.5rem', marginTop: '1rem' }}>
          <select
            value={selectedStructure}
            onChange={(e) => setSelectedStructure(e.target.value)}
            className="admin-select"
          >
            <option value="">Select structure...</option>
            {structures.map((s) => (
              <option key={s.structure} value={s.structure}>
                {s.structure}
              </option>
            ))}
          </select>
          <button
            onClick={handleRefresh}
            disabled={syncing || !selectedStructure || pipelineRunning}
            className="button-primary"
          >
            {syncing ? 'Syncing...' : 'Refresh Cache'}
          </button>
        </div>
      </div>

      <div className="admin-sync-section">
        <h2>Full Sync Pipeline</h2>
        <p>
          Upload instruments CSV and run the complete sync chain:
          CSV upload, proposals fetch, structure generation, Ceph provisioning, local cache refresh.
        </p>

        <div style={{ display: 'flex', gap: '1rem', alignItems: 'center', marginTop: '1rem', flexWrap: 'wrap' }}>
          <input type="file" accept=".csv" ref={fileRef} disabled={pipelineRunning} />
          <label style={{ display: 'flex', alignItems: 'center', gap: '0.35rem', fontSize: '0.875rem', cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={skipProposals}
              onChange={(e) => setSkipProposals(e.target.checked)}
              disabled={pipelineRunning}
            />
            Skip proposals fetch (use existing CouchDB data)
          </label>
        </div>

        <div style={{ marginTop: '0.75rem' }}>
          <button
            onClick={runPipeline}
            disabled={pipelineRunning || syncing}
            className="button-primary"
          >
            {pipelineRunning ? 'Running Pipeline...' : 'Run Full Pipeline'}
          </button>
        </div>

        {(pipelineRunning || steps.some(s => s.status !== 'pending')) && (
          <div style={{ marginTop: '1rem' }}>
            {steps.map((step, i) => (
              <div key={i} style={{
                padding: '0.5rem 0.75rem',
                marginBottom: '0.25rem',
                borderRadius: '4px',
                fontSize: '0.875rem',
                background: step.status === 'running' ? '#eff6ff'
                  : step.status === 'failed' ? '#fef2f2'
                  : step.status === 'done' ? '#f0fdf4'
                  : 'transparent',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                  <span>{statusIcon(step.status)}</span>
                  <span style={{
                    fontWeight: step.status === 'running' ? 600 : 400,
                    color: step.status === 'failed' ? '#dc2626'
                      : step.status === 'skipped' ? '#9ca3af'
                      : 'inherit',
                  }}>
                    {step.label}
                  </span>
                </div>
                {step.result && (
                  <div style={{
                    marginTop: '0.25rem',
                    marginLeft: '1.75rem',
                    fontSize: '0.8rem',
                    color: '#6b7280',
                    whiteSpace: 'pre-wrap',
                    maxHeight: '100px',
                    overflow: 'auto',
                  }}>
                    {step.result}
                  </div>
                )}
                {step.error && (
                  <div style={{
                    marginTop: '0.25rem',
                    marginLeft: '1.75rem',
                    fontSize: '0.8rem',
                    color: '#dc2626',
                  }}>
                    {step.error}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {pipelineRunning && (
          <p style={{ color: '#888', fontSize: '0.8rem', marginTop: '0.5rem' }}>
            This may take a few minutes. Do not close this page.
          </p>
        )}
      </div>
    </div>
  )
}

function summarize(data: unknown): string {
  if (data === null || data === undefined) return 'OK'
  if (typeof data === 'string') return data
  const obj = data as Record<string, unknown>
  // RGWSquared wraps payloads in { execTime, req, res }.
  if (obj.execTime) {
    const res = obj.res
    if (Array.isArray(res)) return `${res.length} items (${obj.execTime})`
    if (res && typeof res === 'object') {
      const r = res as Record<string, unknown>
      if (r.numProposals) return `${r.numProposals} proposals (${obj.execTime})`
      if (r.struct) {
        const buckets = (r.struct as Record<string, unknown>).buckets
        const count = Array.isArray(buckets) ? buckets.length : '?'
        return `Structure generated: ${count} buckets (${obj.execTime})`
      }
      // CSV upload returns the instrument rows under res.data.
      if (Array.isArray(r.data)) return `${r.data.length} instruments uploaded (${obj.execTime})`
      if (r.count) return `${r.count} items (${obj.execTime})`
      return `OK (${obj.execTime})`
    }
    return `OK (${obj.execTime})`
  }
  // Django cache refresh returns per-table sync counts.
  if (obj.users_synced !== undefined) {
    return `${obj.users_synced} users, ${obj.buckets_synced} buckets, ${obj.permissions_synced} permissions synced`
  }
  if (obj.status) return String(obj.message || obj.status)
  return JSON.stringify(data).slice(0, 200)
}

export default SyncView
