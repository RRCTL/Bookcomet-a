import { StrictMode, lazy, Suspense } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Navigate, Routes, Route } from 'react-router-dom'
import './index.css'
import { AuthProvider } from './contexts/AuthContext.tsx'
import { AuthGuard, AuthLoadingScreen } from './components/AuthGuard.tsx'
import { WorkspaceErrorBoundary } from './components/WorkspaceErrorBoundary.tsx'

const LoginPage = lazy(() => import('./pages/LoginPage.tsx'))
const RegisterPage = lazy(() => import('./pages/RegisterPage.tsx'))
/** TF-01/TF-02 UX preview — synthetic geometry only; registered in DEV builds only. */
const TableFirstPreviewPage = import.meta.env.DEV
  ? lazy(() => import('./features/mvduPreview/TableFirstPreviewPage.tsx'))
  : null
/** AQ-01/AQ-02 quality preview — SVG + metric fixtures; DEV builds only. */
const AqQualityPreviewPage = import.meta.env.DEV
  ? lazy(() => import('./features/mvduPreview/AqQualityPreviewPage.tsx'))
  : null
/** Lazy so public routes do not load the workspace graph; workspace is its own chunk. */
const WorkspaceApp = lazy(() => import('./features/workspace/WorkspaceApp'))
const NodeWorkspace = lazy(() => import('./features/nodeWorkspace/NodeWorkspace'))
const ErpShell = lazy(() => import('./features/erpShell/ErpShell'))

const useLegacyWorkspace = import.meta.env.VITE_LEGACY_WORKSPACE === '1'
const LegacyOrNodeShell = useLegacyWorkspace ? WorkspaceApp : NodeWorkspace

/**
 * VITE_UI_THEME selects the application shell:
 *   - 'erp'    -> professional ERP-grid shell (default)
 *   - 'legacy' -> NodeWorkspace / WorkspaceApp restore point
 * Set VITE_UI_THEME=legacy (or VITE_LEGACY_WORKSPACE=1) to roll back.
 */
const uiTheme = import.meta.env.VITE_UI_THEME ?? 'erp'
const WorkspaceShell = uiTheme === 'erp' ? ErpShell : LegacyOrNodeShell

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <Suspense fallback={<AuthLoadingScreen />}>
          <Routes>
            <Route path="/" element={<Navigate to="/login" replace />} />
            <Route path="/login" element={<LoginPage />} />
            <Route path="/register" element={<RegisterPage />} />
            {import.meta.env.DEV && TableFirstPreviewPage ? (
              <Route path="/mvdu-table-first-preview" element={<TableFirstPreviewPage />} />
            ) : null}
            {import.meta.env.DEV && AqQualityPreviewPage ? (
              <Route path="/mvdu-aq-quality-preview" element={<AqQualityPreviewPage />} />
            ) : null}
            <Route
              path="/*"
              element={
                <AuthGuard>
                  <WorkspaceErrorBoundary>
                    <WorkspaceShell />
                  </WorkspaceErrorBoundary>
                </AuthGuard>
              }
            />
          </Routes>
        </Suspense>
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
)
