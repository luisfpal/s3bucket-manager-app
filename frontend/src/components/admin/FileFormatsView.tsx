import { useAutoError } from '../../hooks/useAutoMessage'
import { useState, useEffect } from 'react'
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Legend } from 'recharts'
import { adminAPI, getApiError } from '../../services/api'
import { formatSize } from '../../utils/format'
import type { FileFormatsResponse, FileFormatEntry, AdminAvailableTenant } from '../../types'

const COLORS = [
  '#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6',
  '#06b6d4', '#f97316', '#84cc16', '#ec4899', '#6366f1',
  '#14b8a6', '#d97706', '#64748b', '#a855f7', '#22c55e',
]

type Metric = 'count' | 'size'

interface TooltipPayload {
  name: string;
  value: number;
  payload: FileFormatEntry & { pct: string };
}

function CustomTooltip({ active, payload }: { active?: boolean; payload?: TooltipPayload[] }) {
  if (!active || !payload?.length) return null
  const d = payload[0].payload
  return (
    <div style={{
      background: '#fff', border: '1px solid #e2e8f0', borderRadius: '8px',
      padding: '0.6rem 0.9rem', fontSize: '0.82rem', boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
    }}>
      <div style={{ fontWeight: 700, marginBottom: '0.25rem', color: '#0f172a' }}>.{d.extension}</div>
      <div style={{ color: '#475569' }}>{d.count} file{d.count !== 1 ? 's' : ''}</div>
      <div style={{ color: '#475569' }}>{formatSize(d.size_bytes)}</div>
      <div style={{ color: '#94a3b8' }}>{d.pct}% of total</div>
    </div>
  )
}

function FileFormatsView() {
  const [tenants, setTenants] = useState<AdminAvailableTenant[]>([])
  const [selectedTenant, setSelectedTenant] = useState('')
  const [data, setData] = useState<FileFormatsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadingData, setLoadingData] = useState(false)
  const [error, setError] = useAutoError()
  const [metric, setMetric] = useState<Metric>('count')
  const [selectedEntry, setSelectedEntry] = useState<FileFormatEntry | null>(null)

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
    setSelectedEntry(null)
    adminAPI.getFileFormats(selectedTenant)
      .then(setData)
      .catch(err => setError(getApiError(err, 'Failed to load file formats')))
      .finally(() => setLoadingData(false))
  }, [selectedTenant])

  if (loading) return <div className="admin-loading">Loading…</div>

  const pieData = (data?.formats ?? []).map((f, i) => {
    const total = metric === 'count' ? (data?.total_files ?? 1) : (data?.total_size_bytes ?? 1)
    const val = metric === 'count' ? f.count : f.size_bytes
    return {
      ...f,
      name: `.${f.extension}`,
      value: val,
      pct: total > 0 ? ((val / total) * 100).toFixed(1) : '0.0',
      color: COLORS[i % COLORS.length],
    }
  })

  const breakdownData = selectedEntry
    ? [
        { name: 'Proposal', count: selectedEntry.proposal_count, size: selectedEntry.proposal_size, fill: '#3b82f6' },
        { name: 'Local',    count: selectedEntry.local_count,    size: selectedEntry.local_size,    fill: '#10b981' },
      ]
    : []

  return (
    <div>
      <div className="admin-page-header">
        <h1>File Format Distribution</h1>
        <select
          value={selectedTenant}
          onChange={e => { setSelectedTenant(e.target.value); setSelectedEntry(null) }}
          className="admin-select"
        >
          <option value="">Select tenant…</option>
          {tenants.map(t => <option key={t.structure} value={t.structure}>{t.structure}</option>)}
        </select>
      </div>

      {error && <div className="error-message" onClick={() => setError(null)}>{error}</div>}

      {loadingData ? (
        <div className="admin-loading">Loading file format data…</div>
      ) : !data || data.total_files === 0 ? (
        <div style={{ padding: '1rem 1.25rem', background: '#f8fafc', border: '1px solid #e2e8f0', borderRadius: '6px', color: '#64748b' }}>
          No files uploaded in <strong>{selectedTenant}</strong> yet.
        </div>
      ) : (
        <>
          {/* Summary bar */}
          <div style={{
            display: 'flex', gap: '2rem', padding: '0.75rem 1rem',
            background: '#f1f5f9', borderRadius: '0.5rem', marginBottom: '1rem',
            fontSize: '0.875rem', color: '#475569',
          }}>
            <span><strong>{data.total_files}</strong> files</span>
            <span><strong>{formatSize(data.total_size_bytes)}</strong> total</span>
            <span><strong>{data.formats.length}</strong> format{data.formats.length !== 1 ? 's' : ''}</span>
          </div>

          {/* Metric toggle */}
          <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1rem' }}>
            <button
              className={metric === 'count' ? 'button-primary' : 'button-secondary'}
              style={{ padding: '0.25rem 0.75rem', fontSize: '0.82rem' }}
              onClick={() => setMetric('count')}
            >
              By file count
            </button>
            <button
              className={metric === 'size' ? 'button-primary' : 'button-secondary'}
              style={{ padding: '0.25rem 0.75rem', fontSize: '0.82rem' }}
              onClick={() => setMetric('size')}
            >
              By storage size
            </button>
          </div>

          <div style={{ display: 'flex', gap: '2rem', flexWrap: 'wrap', alignItems: 'flex-start' }}>
            {/* Pie chart */}
            <div style={{ flex: '0 0 340px' }}>
              <div style={{ fontSize: '0.8rem', color: '#94a3b8', marginBottom: '0.5rem', textAlign: 'center' }}>
                Click a slice for proposal vs local breakdown
              </div>
              <ResponsiveContainer width={340} height={300}>
                <PieChart>
                  <Pie
                    data={pieData}
                    cx="50%"
                    cy="50%"
                    outerRadius={120}
                    dataKey="value"
                    onClick={(d) => {
                      const entry = d as unknown as FileFormatEntry
                      setSelectedEntry(selectedEntry?.extension === entry.extension ? null : entry)
                    }}
                    stroke="none"
                  >
                    {pieData.map((entry, i) => (
                      <Cell
                        key={entry.extension}
                        fill={COLORS[i % COLORS.length]}
                        opacity={selectedEntry && selectedEntry.extension !== entry.extension ? 0.4 : 1}
                        style={{ cursor: 'pointer' }}
                      />
                    ))}
                  </Pie>
                  <Tooltip content={<CustomTooltip />} />
                </PieChart>
              </ResponsiveContainer>
              {/* Legend */}
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem', justifyContent: 'center', marginTop: '0.5rem' }}>
                {pieData.map((d, i) => (
                  <span
                    key={d.extension}
                    onClick={() => setSelectedEntry(selectedEntry?.extension === d.extension ? null : data.formats[i])}
                    style={{
                      display: 'flex', alignItems: 'center', gap: '0.25rem',
                      fontSize: '0.78rem', cursor: 'pointer',
                      opacity: selectedEntry && selectedEntry.extension !== d.extension ? 0.4 : 1,
                    }}
                  >
                    <span style={{ width: '10px', height: '10px', borderRadius: '2px', background: COLORS[i % COLORS.length], display: 'inline-block' }} />
                    .{d.extension} ({d.pct}%)
                  </span>
                ))}
              </div>
            </div>

            {/* Detail panel */}
            <div style={{ flex: '1 1 320px', minWidth: '260px' }}>
              {selectedEntry ? (
                <>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '1rem' }}>
                    <h3 style={{ margin: 0 }}>.{selectedEntry.extension}</h3>
                    <button
                      className="button-secondary"
                      style={{ padding: '0.2rem 0.5rem', fontSize: '0.78rem' }}
                      onClick={() => setSelectedEntry(null)}
                    >
                      ← All formats
                    </button>
                  </div>
                  <div style={{ display: 'flex', gap: '1.5rem', marginBottom: '1rem', fontSize: '0.875rem', color: '#475569' }}>
                    <span><strong>{selectedEntry.count}</strong> total files</span>
                    <span><strong>{formatSize(selectedEntry.size_bytes)}</strong> total</span>
                  </div>
                  <ResponsiveContainer width="100%" height={160}>
                    <BarChart data={breakdownData} margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
                      <XAxis dataKey="name" tick={{ fontSize: 12 }} />
                      <YAxis tick={{ fontSize: 11 }} />
                      <Tooltip
                        formatter={(val, name) => {
                          const n = Number(val)
                          return String(name) === 'count'
                            ? [`${n} files`, 'Files'] as [string, string]
                            : [formatSize(n), 'Storage'] as [string, string]
                        }}
                      />
                      <Legend wrapperStyle={{ fontSize: '0.78rem' }} />
                      <Bar dataKey={metric === 'count' ? 'count' : 'size'} name={metric === 'count' ? 'Files' : 'Storage'}>
                        {breakdownData.map((d, i) => <Cell key={i} fill={d.fill} />)}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                  <table style={{ width: '100%', fontSize: '0.82rem', borderCollapse: 'collapse', marginTop: '0.5rem' }}>
                    <thead>
                      <tr style={{ background: '#f1f5f9' }}>
                        <th style={{ padding: '0.3rem 0.6rem', textAlign: 'left' }}>Bucket type</th>
                        <th style={{ padding: '0.3rem 0.6rem', textAlign: 'right' }}>Files</th>
                        <th style={{ padding: '0.3rem 0.6rem', textAlign: 'right' }}>Storage</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr style={{ borderBottom: '1px solid #e2e8f0' }}>
                        <td style={{ padding: '0.3rem 0.6rem' }}>
                          <span style={{ color: '#3b82f6', fontWeight: 600 }}>●</span> Proposal
                        </td>
                        <td style={{ padding: '0.3rem 0.6rem', textAlign: 'right' }}>{selectedEntry.proposal_count}</td>
                        <td style={{ padding: '0.3rem 0.6rem', textAlign: 'right' }}>{formatSize(selectedEntry.proposal_size)}</td>
                      </tr>
                      <tr>
                        <td style={{ padding: '0.3rem 0.6rem' }}>
                          <span style={{ color: '#10b981', fontWeight: 600 }}>●</span> Local
                        </td>
                        <td style={{ padding: '0.3rem 0.6rem', textAlign: 'right' }}>{selectedEntry.local_count}</td>
                        <td style={{ padding: '0.3rem 0.6rem', textAlign: 'right' }}>{formatSize(selectedEntry.local_size)}</td>
                      </tr>
                    </tbody>
                  </table>
                </>
              ) : (
                <div className="admin-table-wrap" style={{ marginTop: 0 }}>
                  <table className="admin-table">
                    <thead>
                      <tr>
                        <th>Format</th>
                        <th style={{ textAlign: 'right' }}>Files</th>
                        <th style={{ textAlign: 'right' }}>Storage</th>
                        <th style={{ textAlign: 'right' }}>Share</th>
                      </tr>
                    </thead>
                    <tbody>
                      {pieData.map((d, i) => (
                        <tr
                          key={d.extension}
                          onClick={() => setSelectedEntry(data.formats[i])}
                          style={{ cursor: 'pointer' }}
                        >
                          <td>
                            <span style={{ display: 'inline-block', width: '10px', height: '10px', borderRadius: '2px', background: COLORS[i % COLORS.length], marginRight: '0.5rem' }} />
                            .{d.extension}
                          </td>
                          <td style={{ textAlign: 'right' }}>{d.count}</td>
                          <td style={{ textAlign: 'right' }}>{formatSize(d.size_bytes)}</td>
                          <td style={{ textAlign: 'right', color: '#94a3b8' }}>{d.pct}%</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <div className="admin-table-footer">Click a row to see proposal vs local breakdown</div>
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  )
}

export default FileFormatsView
