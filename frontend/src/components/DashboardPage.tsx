import { useState, useEffect, useCallback } from 'react'
import { apiFetch } from '../services/api'
import './DashboardPage.css'

type Period = 'week' | 'month' | 'all'

interface ModeStats {
  tasks: number
  pages: number
  files: number
}

interface DashboardStats {
  period: string
  total_tasks: number
  total_pages: number
  total_files: number
  ai_completion_rate: number | null
  estimated_cost_usd: number
  by_mode: Record<string, ModeStats>
}

const MODE_CONFIG: Record<string, { label: string; color: string }> = {
  AR:        { label: 'AR — Receivables',    color: '#22c55e' },
  AP:        { label: 'AP — Payables',    color: '#eab308' },
  BANK:      { label: 'BANK — Statements', color: '#9333ea' },
  OTHER: { label: 'Other',     color: '#3b82f6' },
}

const PERIOD_OPTIONS: { value: Period; label: string }[] = [
  { value: 'week',  label: 'This week' },
  { value: 'month', label: 'This month' },
  { value: 'all',   label: 'All' },
]

function formatCost(usd: number): string {
  if (usd === 0) return '$0.00'
  if (usd < 0.001) return `$${(usd * 1000).toFixed(3)}m`
  return `$${usd.toFixed(4)}`
}

function formatRate(rate: number | null): string {
  if (rate === null) return '—'
  return `${Math.round(rate * 100)}%`
}

interface Props {
  onClose: () => void
}

const EMPTY_STATS: DashboardStats = {
  period: 'month',
  total_tasks: 0,
  total_pages: 0,
  total_files: 0,
  ai_completion_rate: null,
  estimated_cost_usd: 0,
  by_mode: {},
}

export function DashboardPage({ onClose }: Props) {
  const [period, setPeriod] = useState<Period>('month')
  const [stats, setStats] = useState<DashboardStats>(EMPTY_STATS)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchStats = useCallback(async (p: Period) => {
    setLoading(true)
    setError(null)
    try {
      const res = await apiFetch(`/api/dashboard/stats?period=${p}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data: DashboardStats = await res.json()
      setStats(data)
    } catch {
      setError('Could not reach the backend. Showing empty stats.')
      setStats({ ...EMPTY_STATS, period: p })
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchStats(period)
  }, [period, fetchStats])

  const displayModes = ['AR', 'AP', 'BANK', 'OTHER']

  return (
    <div className="dashboard-overlay">
      <div className="dashboard-page">

        {/* Header */}
        <div className="dashboard-header">
          <div className="dashboard-header-left">
            <h1 className="dashboard-title">Efficiency dashboard</h1>
            <p className="dashboard-subtitle">Track AI-assisted workload</p>
          </div>
          <div className="dashboard-header-right">
            <div className="period-toggle">
              {PERIOD_OPTIONS.map(opt => (
                <button
                  key={opt.value}
                  className={`period-btn${period === opt.value ? ' active' : ''}`}
                  onClick={() => setPeriod(opt.value)}
                >
                  {opt.label}
                </button>
              ))}
            </div>
            <button className="dashboard-close" onClick={onClose} title="Close">✕</button>
          </div>
        </div>

        {/* Inline error banner — non-blocking */}
        {error && !loading && (
          <div className="dashboard-error-banner">{error}</div>
        )}

        {/* Content — always rendered, shows zeros when no data */}
        <div className={`dashboard-content${loading ? ' dashboard-content--loading' : ''}`}>

          {/* Top metric cards */}
          <div className="metric-cards">
            <div className="metric-card">
              <div className="metric-card-icon metric-card-icon--pages">Pg</div>
              <div className="metric-card-body">
                <div className="metric-value">
                  {loading ? <span className="metric-skeleton" /> : stats.total_pages.toLocaleString()}
                </div>
                <div className="metric-label">Pages processed</div>
                <div className="metric-sub">
                  {loading ? <span className="metric-skeleton metric-skeleton--sm" /> : `${stats.total_files.toLocaleString()} files · ${stats.total_tasks.toLocaleString()} tasks`}
                </div>
              </div>
            </div>

            <div className="metric-card">
              <div className="metric-card-icon metric-card-icon--ai">AI</div>
              <div className="metric-card-body">
                <div className="metric-value">
                  {loading ? <span className="metric-skeleton" /> : formatRate(stats.ai_completion_rate)}
                </div>
                <div className="metric-label">AI completion rate</div>
                <div className="metric-sub">
                  {loading
                    ? <span className="metric-skeleton metric-skeleton--sm" />
                    : stats.ai_completion_rate === null ? 'No OCR data yet' : 'Share of pages enhanced by AI'}
                </div>
              </div>
              {!loading && stats.ai_completion_rate !== null && (
                <div className="metric-card-bar">
                  <div
                    className="metric-card-bar-fill"
                    style={{ width: `${Math.round(stats.ai_completion_rate * 100)}%` }}
                  />
                </div>
              )}
              {!loading && stats.ai_completion_rate === null && (
                <div className="metric-card-bar">
                  <div className="metric-card-bar-fill" style={{ width: '0%' }} />
                </div>
              )}
            </div>

            <div className="metric-card">
              <div className="metric-card-icon metric-card-icon--cost">$</div>
              <div className="metric-card-body">
                <div className="metric-value metric-value--small">
                  {loading ? <span className="metric-skeleton" /> : formatCost(stats.estimated_cost_usd)}
                </div>
                <div className="metric-label">AI cost (USD)</div>
                <div className="metric-sub">Chat, OCR enhance, and recon AI</div>
              </div>
            </div>
          </div>

          {/* Mode breakdown */}
          <div className="mode-section">
            <h2 className="mode-section-title">Usage by mode</h2>
            <div className="mode-cards">
              {displayModes.map(mode => {
                const cfg = MODE_CONFIG[mode]
                const modeStats = stats.by_mode[mode] ?? { tasks: 0, pages: 0, files: 0 }
                const isEmpty = modeStats.tasks === 0
                return (
                  <div key={mode} className={`mode-card${isEmpty ? ' mode-card--empty' : ''}`}>
                    <div className="mode-card-header">
                      <span className="mode-card-dot" style={{ background: isEmpty ? '#e5e7eb' : cfg.color }} />
                      <span className="mode-card-label">{cfg.label}</span>
                      <span
                        className="mode-card-badge"
                        style={{ background: isEmpty ? '#e5e7eb' : cfg.color + '20', color: isEmpty ? '#9ca3af' : cfg.color }}
                      >
                        {loading ? '—' : `${modeStats.tasks} tasks`}
                      </span>
                    </div>
                    <div className="mode-card-stats">
                      <div className="mode-stat">
                        <div className="mode-stat-value">
                          {loading ? <span className="metric-skeleton metric-skeleton--sm" /> : modeStats.pages.toLocaleString()}
                        </div>
                        <div className="mode-stat-label">Pages</div>
                      </div>
                      <div className="mode-stat-divider" />
                      <div className="mode-stat">
                        <div className="mode-stat-value">
                          {loading ? <span className="metric-skeleton metric-skeleton--sm" /> : modeStats.files.toLocaleString()}
                        </div>
                        <div className="mode-stat-label">Files</div>
                      </div>
                    </div>
                    {!isEmpty && !loading && (
                      <div
                        className="mode-card-accent"
                        style={{ background: cfg.color }}
                      />
                    )}
                  </div>
                )
              })}
            </div>
          </div>

          {/* Footer note */}
          <p className="dashboard-footer-note">
            * Completion rate comes from OCR events. Cost is an estimate of AI API usage.
          </p>
        </div>
      </div>
    </div>
  )
}
