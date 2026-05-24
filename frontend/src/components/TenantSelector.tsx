import { useAutoError } from '../hooks/useAutoMessage'
/**
 * TenantSelector — shown after login when user has multiple tenants.
 */

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { authAPI } from '../services/api'
import type { TenantInfo } from '../types'

interface TenantSelectorProps {
  tenants: TenantInfo[];
}

function TenantSelector({ tenants }: TenantSelectorProps) {
  const navigate = useNavigate()
  const [selecting, setSelecting] = useState(false)
  const [error, setError] = useAutoError()

  const handleSelect = async (tenant: TenantInfo) => {
    try {
      setSelecting(true)
      await authAPI.selectTenant(tenant.id)
      authAPI.setActiveTenant(tenant)
      navigate('/dashboard', { replace: true })
    } catch (err: unknown) {
      const message = (err as { response?: { data?: { error?: string } } })?.response?.data?.error || 'Failed to select tenant'
      setError(message)
    } finally {
      setSelecting(false)
    }
  }

  return (
    <div className="page-container">
      <div className="auth-container">
        <div className="auth-card" style={{ maxWidth: '500px' }}>
          <h2>Select Your Area</h2>
          <p style={{ color: '#666', marginBottom: '1.5rem' }}>
            You have access to multiple research areas. Select one to continue.
          </p>

          {error && (
            <div className="error-message" style={{ marginBottom: '1rem' }}>{error}</div>
          )}

          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
            {tenants.map((tenant) => (
              <button
                key={tenant.id}
                className="button-primary"
                style={{ padding: '1rem', fontSize: '1rem', textAlign: 'left' }}
                onClick={() => handleSelect(tenant)}
                disabled={selecting}
              >
                <strong>{tenant.name}</strong>
                <span style={{ float: 'right', opacity: 0.7, fontSize: '0.85rem' }}>
                  {tenant.role === 'rw' ? 'Read-Write' : tenant.role === 'ro' ? 'Read-Only' : 'Admin'}
                </span>
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

export default TenantSelector
