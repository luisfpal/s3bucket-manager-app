/**
 * TenantSelectorPage — loads pending tenants from localStorage
 * and renders the TenantSelector component.
 */

import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { authAPI } from '../services/api'
import TenantSelector from './TenantSelector'
import type { TenantInfo } from '../types'

function TenantSelectorPage() {
  const navigate = useNavigate()
  const [tenants, setTenants] = useState<TenantInfo[]>([])

  useEffect(() => {
    const activeTenant = authAPI.getActiveTenant()
    if (activeTenant) {
      navigate('/dashboard', { replace: true })
      return
    }

    // AuthCallback passes the tenant list through localStorage across the redirect.
    const pendingRaw = localStorage.getItem('pending_tenants')
    if (pendingRaw) {
      try {
        const parsed = JSON.parse(pendingRaw)
        setTenants(parsed)
        localStorage.removeItem('pending_tenants')
      } catch {
        navigate('/login', { replace: true })
      }
    } else {
      // Reload tenants when the user opens this route directly.
      authAPI.getCurrentUser().then(user => {
        if (user.tenants && user.tenants.length > 1) {
          setTenants(user.tenants)
        } else if (user.tenants && user.tenants.length === 1) {
          authAPI.setActiveTenant(user.tenants[0])
          navigate('/dashboard', { replace: true })
        } else {
          navigate('/login', { replace: true })
        }
      }).catch(() => navigate('/login', { replace: true }))
    }
  }, [navigate])

  if (tenants.length === 0) return null

  return <TenantSelector tenants={tenants} />
}

export default TenantSelectorPage
