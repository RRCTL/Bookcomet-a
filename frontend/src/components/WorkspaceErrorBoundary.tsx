import { Component, type CSSProperties, type ErrorInfo, type ReactNode } from 'react'
import { api } from '../services/api'
import { snapshotAndClearTabBackgroundJobIds } from '../services/tabBackgroundJobRegistry'

type Props = { children: ReactNode }

type State = { hasError: boolean; error: Error | null }

const shellStyle: CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  justifyContent: 'center',
  minHeight: '100vh',
  padding: 24,
  background: '#f3f4f6',
  color: '#374151',
  fontSize: 14,
  lineHeight: 1.5,
  fontFamily: 'inherit',
  textAlign: 'center',
  boxSizing: 'border-box',
}

/**
 * Catches render errors from the lazy-loaded workspace (including failed dynamic imports).
 * Without this, React can leave a blank viewport when the workspace chunk throws.
 */
export class WorkspaceErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('[WorkspaceErrorBoundary]', error, info.componentStack)
    const ids = snapshotAndClearTabBackgroundJobIds()
    if (ids.length === 0) return
    void Promise.all(
      ids.map((id) =>
        api.cancelBackgroundJob(id).catch((e) =>
          console.warn('[WorkspaceErrorBoundary] cancel background job failed', id, e),
        ),
      ),
    )
  }

  handleRetry = (): void => {
    this.setState({ hasError: false, error: null })
  }

  render(): ReactNode {
    if (this.state.hasError) {
      const msg = this.state.error?.message?.trim() || 'Unknown error'
      return (
        <div style={shellStyle}>
          <h1 style={{ margin: '0 0 12px', fontSize: 18, fontWeight: 700, color: '#111827' }}>
            {'\u7121\u6cd5\u8f09\u5165\u5de5\u4f5c\u53f0'} &middot; Workspace failed to load
          </h1>
          <p style={{ margin: '0 0 8px', maxWidth: 420 }}>
            {
              '\u5e38\u898b\u539f\u56e0\uff1a\u7db2\u8def\u4e2d\u65b7\u6216\u701b\u89bd\u5668\u5feb\u53d6\u3002\u8acb\u5148\u91cd\u65b0\u6574\u7406\u9801\u9762\uff1b\u82e5\u4ecd\u5931\u6557\uff0c\u8acb\u67e5\u770b\u958b\u767c\u8005\u5de5\u5177 Console\u3002'
            }
          </p>
          <p style={{ margin: '0 0 20px', maxWidth: 420, fontSize: 12, color: '#6b7280' }}>
            Often a network or browser-cache issue. Hard-refresh or check the Console for details.
          </p>
          <pre
            style={{
              margin: '0 0 20px',
              padding: 12,
              maxWidth: 'min(560px, 100%)',
              overflow: 'auto',
              background: '#fff',
              border: '1px solid #e5e7eb',
              borderRadius: 8,
              fontSize: 11,
              color: '#b91c1c',
              textAlign: 'left',
            }}
          >
            {msg}
          </pre>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, justifyContent: 'center' }}>
            <button
              type="button"
              onClick={() => window.location.reload()}
              style={{
                padding: '10px 18px',
                fontWeight: 600,
                borderRadius: 8,
                border: '1px solid #111827',
                background: '#111827',
                color: '#fff',
                cursor: 'pointer',
                fontFamily: 'inherit',
                fontSize: 14,
              }}
            >
              {'\u91cd\u65b0\u6574\u7406'} &middot; Reload
            </button>
            <button
              type="button"
              onClick={this.handleRetry}
              style={{
                padding: '10px 18px',
                fontWeight: 600,
                borderRadius: 8,
                border: '1px solid #cbd5e1',
                background: '#fff',
                color: '#374151',
                cursor: 'pointer',
                fontFamily: 'inherit',
                fontSize: 14,
              }}
            >
              {'\u518d\u8a66\u4e00\u6b21'} &middot; Try again
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
