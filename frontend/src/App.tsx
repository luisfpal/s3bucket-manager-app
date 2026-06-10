/**
 * App.tsx — React Router Configuration (Multi-Tenant)
 *
 * Routes:
 * - /login              — public login page
 * - /auth/callback      — OAuth2 → JWT bridge
 * - /select-tenant      — tenant picker for multi-tenant users
 * - /dashboard          — tenant-scoped bucket list
 * - /buckets/:id        — bucket detail + file browser
 * - /profile            — user profile
 * - /admin/login        — admin Authentik login
 * - /admin/auth/callback — admin OAuth → JWT bridge
 * - /admin/*            — admin panel
 */

import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import Login from './components/Login'
import AuthCallback from './components/AuthCallback'
import TenantSelectorPage from './components/TenantSelectorPage'
import Dashboard from './components/Dashboard'
import BucketDetail from './components/BucketDetail'
import NexusViewer from './components/NexusViewer'
import Profile from './components/Profile'
import TenantDocPage from './components/TenantDocPage'
import AdminLogin from './components/admin/AdminLogin'
import AdminAuthCallback from './components/admin/AdminAuthCallback'
import AdminLayout from './components/admin/AdminLayout'
import BucketsView from './components/admin/BucketsView'
import UsersView from './components/admin/UsersView'
import UOMappingsView from './components/admin/UOMappingsView'
import TenantsView from './components/admin/TenantsView'
import SyncView from './components/admin/SyncView'
import FileNameRulesView from './components/admin/FileNameRulesView'
import FileDeviationsView from './components/admin/FileDeviationsView'
import TenantDocView from './components/admin/TenantDocView'
import FileFormatsView from './components/admin/FileFormatsView'
import { authStorage } from './services/api'

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const token = authStorage.isAuthenticated()
  return token ? <>{children}</> : <Navigate to="/login" replace />
}

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/auth/callback" element={<AuthCallback />} />

        <Route path="/select-tenant" element={<ProtectedRoute><TenantSelectorPage /></ProtectedRoute>} />

        <Route path="/dashboard" element={<ProtectedRoute><Dashboard /></ProtectedRoute>} />
        <Route path="/buckets/:id" element={<ProtectedRoute><BucketDetail /></ProtectedRoute>} />
        <Route path="/buckets/:id/nexus" element={<ProtectedRoute><NexusViewer /></ProtectedRoute>} />
        <Route path="/profile" element={<ProtectedRoute><Profile /></ProtectedRoute>} />
        <Route path="/guide" element={<ProtectedRoute><TenantDocPage /></ProtectedRoute>} />

        <Route path="/admin/login" element={<AdminLogin />} />
        <Route path="/admin/auth/callback" element={<AdminAuthCallback />} />
        <Route path="/admin" element={<AdminLayout />}>
          <Route index element={<Navigate to="/admin/buckets" replace />} />
          <Route path="buckets" element={<BucketsView />} />
          <Route path="users" element={<UsersView />} />
          <Route path="uo-mappings" element={<UOMappingsView />} />
          <Route path="tenants" element={<TenantsView />} />
          <Route path="sync" element={<SyncView />} />
          <Route path="file-rules" element={<FileNameRulesView />} />
          <Route path="file-deviations" element={<FileDeviationsView />} />
          <Route path="file-formats" element={<FileFormatsView />} />
          <Route path="tenant-docs" element={<TenantDocView />} />
        </Route>

        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="*" element={
          <div style={{ padding: '2rem', textAlign: 'center' }}>
            <h1>404 - Page Not Found</h1>
            <a href="/dashboard">Go to Dashboard</a>
          </div>
        } />
      </Routes>
    </BrowserRouter>
  )
}

export default App
