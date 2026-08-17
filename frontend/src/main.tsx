import { StrictMode, lazy, Suspense } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import './index.css'
import { AuthProvider } from './contexts/AuthContext.tsx'
import { AuthGuard, AuthLoadingScreen } from './components/AuthGuard.tsx'
import { WorkspaceErrorBoundary } from './components/WorkspaceErrorBoundary.tsx'

const LandingPage = lazy(() => import('./pages/LandingPage.tsx'))
const LoginPage = lazy(() => import('./pages/LoginPage.tsx'))
const RegisterPage = lazy(() => import('./pages/RegisterPage.tsx'))
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
            <Route path="/" element={<LandingPage />} />
            <Route path="/login" element={<LoginPage />} />
            <Route path="/register" element={<RegisterPage />} />
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
