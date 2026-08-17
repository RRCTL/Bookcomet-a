import { useEffect, useRef, useState } from 'react'
import { useAuth } from '../../contexts/AuthContext'
import { api } from '../../services/api'
import { authApi } from '../../services/authApi'
import { getLoginFlowErrorMessage } from '../../utils/httpErrorMessage'
import { useErpBackgroundJobs } from './erpBackgroundJobs'

type Props = {
  onToggleMenu: () => void
}

export function AppTopBar({ onToggleMenu }: Props) {
  const { user, accessToken, activeCompany, companies, switchCompany, refreshCompanies, applyUser, logout } =
    useAuth()
  const { activeJobs } = useErpBackgroundJobs()
  const [workspaceMenuOpen, setWorkspaceMenuOpen] = useState(false)
  const [addingWorkspace, setAddingWorkspace] = useState(false)
  const [newWorkspaceName, setNewWorkspaceName] = useState('')
  const [workspaceBusy, setWorkspaceBusy] = useState(false)
  const [workspaceError, setWorkspaceError] = useState<string | null>(null)
  const workspaceMenuRef = useRef<HTMLDivElement>(null)

  const [accountOpen, setAccountOpen] = useState(false)
  const [displayName, setDisplayName] = useState('')
  const [nameBusy, setNameBusy] = useState(false)
  const [nameStatus, setNameStatus] = useState<string | null>(null)
  const [nameError, setNameError] = useState<string | null>(null)
  const [oldPassword, setOldPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [pwBusy, setPwBusy] = useState(false)
  const [pwStatus, setPwStatus] = useState<string | null>(null)
  const [pwError, setPwError] = useState<string | null>(null)
  const [mfaBusy, setMfaBusy] = useState(false)
  const [mfaStatus, setMfaStatus] = useState<string | null>(null)
  const [mfaError, setMfaError] = useState<string | null>(null)
  const [mfaSecret, setMfaSecret] = useState<string | null>(null)
  const [mfaOtpauth, setMfaOtpauth] = useState<string | null>(null)
  const [mfaCode, setMfaCode] = useState('')
  const [mfaDisablePassword, setMfaDisablePassword] = useState('')
  const [sessionBusy, setSessionBusy] = useState(false)
  const [sessionStatus, setSessionStatus] = useState<string | null>(null)
  const [sessionError, setSessionError] = useState<string | null>(null)
  const accountMenuRef = useRef<HTMLDivElement>(null)

  const initials = (user?.display_name || user?.username || user?.email || 'U')
    .split(/\s+/)
    .map(p => p[0])
    .join('')
    .slice(0, 2)
    .toUpperCase()

  const workspaceLabel = activeCompany?.name ?? 'Select workspace'

  useEffect(() => {
    if (!workspaceMenuOpen) return
    const handleClickOutside = (event: MouseEvent) => {
      if (workspaceMenuRef.current && !workspaceMenuRef.current.contains(event.target as Node)) {
        setWorkspaceMenuOpen(false)
        setAddingWorkspace(false)
        setWorkspaceError(null)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [workspaceMenuOpen])

  useEffect(() => {
    if (!accountOpen) return
    const handleClickOutside = (event: MouseEvent) => {
      if (accountMenuRef.current && !accountMenuRef.current.contains(event.target as Node)) {
        setAccountOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [accountOpen])

  const openAccount = () => {
    setWorkspaceMenuOpen(false)
    setAccountOpen(open => {
      const next = !open
      if (next) {
        setDisplayName(user?.display_name ?? '')
        setNameStatus(null)
        setNameError(null)
        setOldPassword('')
        setNewPassword('')
        setConfirmPassword('')
        setPwStatus(null)
        setPwError(null)
        setMfaStatus(null)
        setMfaError(null)
        setMfaSecret(null)
        setMfaOtpauth(null)
        setMfaCode('')
        setMfaDisablePassword('')
        setSessionStatus(null)
        setSessionError(null)
      }
      return next
    })
  }

  const startMfaSetup = async () => {
    if (!accessToken || mfaBusy) return
    setMfaBusy(true)
    setMfaError(null)
    setMfaStatus(null)
    try {
      const setup = await authApi.mfaSetup(accessToken)
      setMfaSecret(setup.secret)
      setMfaOtpauth(setup.otpauth_url)
      setMfaStatus(
        'In your authenticator, enter the Secret key only (not the otpauth URL). Then type a 6-digit code below.',
      )
    } catch (err) {
      setMfaError(getLoginFlowErrorMessage(err))
    } finally {
      setMfaBusy(false)
    }
  }

  const enableMfa = async () => {
    if (!accessToken || mfaBusy || !mfaCode.trim()) return
    setMfaBusy(true)
    setMfaError(null)
    try {
      await authApi.mfaEnable(accessToken, mfaCode.trim())
      const profile = await authApi.getMe(accessToken)
      applyUser(profile)
      setMfaSecret(null)
      setMfaOtpauth(null)
      setMfaCode('')
      setMfaStatus('MFA enabled.')
    } catch (err) {
      setMfaError(getLoginFlowErrorMessage(err))
    } finally {
      setMfaBusy(false)
    }
  }

  const disableMfa = async () => {
    if (!accessToken || mfaBusy) return
    setMfaBusy(true)
    setMfaError(null)
    try {
      await authApi.mfaDisable(accessToken, mfaDisablePassword, mfaCode.trim())
      const profile = await authApi.getMe(accessToken)
      applyUser(profile)
      setMfaCode('')
      setMfaDisablePassword('')
      setMfaStatus('MFA disabled.')
    } catch (err) {
      setMfaError(getLoginFlowErrorMessage(err))
    } finally {
      setMfaBusy(false)
    }
  }

  const revokeAllSessions = async () => {
    if (!accessToken || sessionBusy) return
    setSessionBusy(true)
    setSessionError(null)
    setSessionStatus(null)
    try {
      await authApi.revokeSessions(accessToken)
      setSessionStatus('All sessions revoked. Sign in again.')
      await logout()
    } catch (err) {
      setSessionError(getLoginFlowErrorMessage(err))
    } finally {
      setSessionBusy(false)
    }
  }

  const saveDisplayName = async () => {
    if (!accessToken || nameBusy) return
    const next = displayName.trim()
    if (!next) {
      setNameError('Display name is required.')
      return
    }
    if (next === (user?.display_name ?? '')) {
      setNameStatus('No changes to save.')
      setNameError(null)
      return
    }
    setNameBusy(true)
    setNameError(null)
    setNameStatus(null)
    try {
      const profile = await authApi.updateProfile(accessToken, next)
      applyUser(profile)
      setDisplayName(profile.display_name)
      setNameStatus('Display name saved.')
    } catch (err) {
      setNameError(getLoginFlowErrorMessage(err))
    } finally {
      setNameBusy(false)
    }
  }

  const changePassword = async () => {
    if (!accessToken || pwBusy) return
    setPwError(null)
    setPwStatus(null)
    if (!oldPassword || !newPassword) {
      setPwError('Enter your current and new password.')
      return
    }
    if (newPassword !== confirmPassword) {
      setPwError('New passwords do not match.')
      return
    }
    if (newPassword.length < 8) {
      setPwError('Password must be at least 8 characters.')
      return
    }
    setPwBusy(true)
    try {
      await authApi.changePassword(accessToken, oldPassword, newPassword)
      setOldPassword('')
      setNewPassword('')
      setConfirmPassword('')
      setPwStatus('Password changed. Please sign in again.')
      await logout()
    } catch (err) {
      setPwError(getLoginFlowErrorMessage(err))
    } finally {
      setPwBusy(false)
    }
  }

  const addWorkspace = async () => {
    const name = newWorkspaceName.trim()
    if (!name || workspaceBusy) return
    setWorkspaceBusy(true)
    setWorkspaceError(null)
    try {
      const row = await api.createCompany(name)
      localStorage.setItem('activeCompanyId', row.id)
      await refreshCompanies()
      switchCompany(row.id)
      setNewWorkspaceName('')
      setAddingWorkspace(false)
      setWorkspaceMenuOpen(false)
    } catch (err) {
      setWorkspaceError(err instanceof Error ? err.message : 'Could not create workspace.')
    } finally {
      setWorkspaceBusy(false)
    }
  }

  return (
    <header className="erp-topbar">
      <button type="button" className="erp-iconbtn erp-menu-toggle" onClick={onToggleMenu} aria-label="Toggle menu">
        &#9776;
      </button>
      <div className="erp-brand">Book<span>comet</span></div>
      <div className="erp-spacer" />
      {activeJobs.length > 0 ? (
        <span className="erp-jobs-indicator" title={activeJobs.map(j => j.progress_label || j.job_type).join(', ')}>
          {activeJobs.length} background job{activeJobs.length === 1 ? '' : 's'}
        </span>
      ) : null}
      <div className="erp-company-menu-wrap" ref={workspaceMenuRef}>
        <button
          type="button"
          className="erp-company erp-company-trigger"
          onClick={() => {
            setAccountOpen(false)
            setWorkspaceMenuOpen(open => !open)
          }}
          aria-expanded={workspaceMenuOpen}
          aria-haspopup="listbox"
          aria-label="Active workspace"
        >
          <span className="erp-company-trigger-label">{workspaceLabel}</span>
          <span className="erp-company-trigger-chevron" aria-hidden>
            v
          </span>
        </button>
        {workspaceMenuOpen ? (
          <div className="erp-company-menu" role="listbox" aria-label="Workspaces">
            <div className="erp-company-menu-list">
              {companies.map(company => (
                <button
                  key={company.id}
                  type="button"
                  role="option"
                  aria-selected={company.id === activeCompany?.id}
                  className={`erp-company-menu-item${company.id === activeCompany?.id ? ' active' : ''}`}
                  onClick={() => {
                    switchCompany(company.id)
                    setWorkspaceMenuOpen(false)
                    setAddingWorkspace(false)
                    setWorkspaceError(null)
                  }}
                >
                  <span className="erp-company-menu-item-name">{company.name}</span>
                  <span className="erp-company-menu-item-meta">
                    {company.id === activeCompany?.id ? 'Active' : company.roleLabel}
                  </span>
                </button>
              ))}
            </div>
            <div className="erp-company-menu-divider" />
            {addingWorkspace ? (
              <div className="erp-company-menu-add">
                <input
                  type="text"
                  className="erp-company-menu-input"
                  autoFocus
                  placeholder="Workspace name"
                  value={newWorkspaceName}
                  disabled={workspaceBusy}
                  onChange={e => setNewWorkspaceName(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter') void addWorkspace()
                    if (e.key === 'Escape') {
                      setAddingWorkspace(false)
                      setWorkspaceError(null)
                    }
                  }}
                />
                <button
                  type="button"
                  className="erp-btn primary erp-company-menu-add-btn"
                  disabled={!newWorkspaceName.trim() || workspaceBusy}
                  onClick={() => void addWorkspace()}
                >
                  {workspaceBusy ? 'Adding…' : 'Add'}
                </button>
                {workspaceError ? (
                  <div className="erp-company-menu-error" role="alert">
                    {workspaceError}
                  </div>
                ) : null}
              </div>
            ) : (
              <button
                type="button"
                className="erp-company-menu-create"
                onClick={() => {
                  setAddingWorkspace(true)
                  setWorkspaceError(null)
                  setNewWorkspaceName('')
                }}
              >
                + Add workspace
              </button>
            )}
          </div>
        ) : null}
      </div>
      <div className="erp-account-wrap" ref={accountMenuRef}>
        <button
          type="button"
          className="erp-avatar"
          onClick={openAccount}
          aria-expanded={accountOpen}
          aria-haspopup="dialog"
          aria-label="Account settings"
          title={user?.email || user?.username || 'Account settings'}
        >
          {initials}
        </button>
        {accountOpen ? (
          <div className="erp-account-menu" role="dialog" aria-label="Account settings">
            <div className="erp-account-menu-title">Account</div>
            <p className="erp-account-meta">
              Username <strong>{user?.username || '—'}</strong>
            </p>
            <label className="erp-account-field">
              <span>Display name</span>
              <input
                type="text"
                value={displayName}
                onChange={e => setDisplayName(e.target.value)}
                disabled={nameBusy}
                autoComplete="name"
              />
            </label>
            <button
              type="button"
              className="erp-btn primary erp-account-action"
              disabled={nameBusy || !displayName.trim()}
              onClick={() => void saveDisplayName()}
            >
              {nameBusy ? 'Saving…' : 'Save name'}
            </button>
            {nameError ? (
              <div className="erp-account-msg erp-account-msg--error" role="alert">
                {nameError}
              </div>
            ) : null}
            {nameStatus ? <div className="erp-account-msg">{nameStatus}</div> : null}

            <div className="erp-company-menu-divider" />
            <div className="erp-account-menu-subtitle">Change password</div>
            <label className="erp-account-field">
              <span>Current password</span>
              <input
                type="password"
                value={oldPassword}
                onChange={e => setOldPassword(e.target.value)}
                disabled={pwBusy}
                autoComplete="current-password"
              />
            </label>
            <label className="erp-account-field">
              <span>New password</span>
              <input
                type="password"
                value={newPassword}
                onChange={e => setNewPassword(e.target.value)}
                disabled={pwBusy}
                autoComplete="new-password"
              />
            </label>
            <label className="erp-account-field">
              <span>Confirm new password</span>
              <input
                type="password"
                value={confirmPassword}
                onChange={e => setConfirmPassword(e.target.value)}
                disabled={pwBusy}
                autoComplete="new-password"
              />
            </label>
            <button
              type="button"
              className="erp-btn primary erp-account-action"
              disabled={pwBusy || !oldPassword || !newPassword || !confirmPassword}
              onClick={() => void changePassword()}
            >
              {pwBusy ? 'Updating…' : 'Update password'}
            </button>
            {pwError ? (
              <div className="erp-account-msg erp-account-msg--error" role="alert">
                {pwError}
              </div>
            ) : null}
            {pwStatus ? <div className="erp-account-msg">{pwStatus}</div> : null}

            <div className="erp-company-menu-divider" />
            <div className="erp-account-menu-subtitle">Two-factor authentication (optional)</div>
            <p className="erp-account-meta">
              Status: <strong>{user?.mfa_enabled ? 'On' : 'Off'}</strong>
            </p>
            {!user?.mfa_enabled ? (
              <>
                <button
                  type="button"
                  className="erp-btn primary erp-account-action"
                  disabled={mfaBusy}
                  onClick={() => void startMfaSetup()}
                >
                  {mfaBusy ? 'Working…' : 'Set up authenticator'}
                </button>
                {mfaSecret ? (
                  <>
                    <p className="erp-account-meta" style={{ wordBreak: 'break-all' }}>
                      Secret key (paste this into the authenticator): <strong>{mfaSecret}</strong>
                    </p>
                    {mfaOtpauth ? (
                      <p className="erp-account-meta" style={{ wordBreak: 'break-all', fontSize: 11 }}>
                        Optional otpauth URL (for apps that accept a full URI / QR): {mfaOtpauth}
                      </p>
                    ) : null}
                    <label className="erp-account-field">
                      <span>Confirm code</span>
                      <input
                        type="text"
                        inputMode="numeric"
                        value={mfaCode}
                        onChange={e => setMfaCode(e.target.value)}
                        disabled={mfaBusy}
                        autoComplete="one-time-code"
                      />
                    </label>
                    <button
                      type="button"
                      className="erp-btn primary erp-account-action"
                      disabled={mfaBusy || !mfaCode.trim()}
                      onClick={() => void enableMfa()}
                    >
                      Enable MFA
                    </button>
                  </>
                ) : null}
              </>
            ) : (
              <>
                <label className="erp-account-field">
                  <span>Current password</span>
                  <input
                    type="password"
                    value={mfaDisablePassword}
                    onChange={e => setMfaDisablePassword(e.target.value)}
                    disabled={mfaBusy}
                    autoComplete="current-password"
                  />
                </label>
                <label className="erp-account-field">
                  <span>Authenticator code</span>
                  <input
                    type="text"
                    inputMode="numeric"
                    value={mfaCode}
                    onChange={e => setMfaCode(e.target.value)}
                    disabled={mfaBusy}
                    autoComplete="one-time-code"
                  />
                </label>
                <button
                  type="button"
                  className="erp-btn erp-account-action"
                  disabled={mfaBusy || !mfaDisablePassword || !mfaCode.trim()}
                  onClick={() => void disableMfa()}
                >
                  Disable MFA
                </button>
              </>
            )}
            {mfaError ? (
              <div className="erp-account-msg erp-account-msg--error" role="alert">
                {mfaError}
              </div>
            ) : null}
            {mfaStatus ? <div className="erp-account-msg">{mfaStatus}</div> : null}

            <div className="erp-company-menu-divider" />
            <div className="erp-account-menu-subtitle">Sessions</div>
            <button
              type="button"
              className="erp-btn erp-account-action"
              disabled={sessionBusy}
              onClick={() => void revokeAllSessions()}
            >
              {sessionBusy ? 'Revoking…' : 'Sign out all sessions'}
            </button>
            {sessionError ? (
              <div className="erp-account-msg erp-account-msg--error" role="alert">
                {sessionError}
              </div>
            ) : null}
            {sessionStatus ? <div className="erp-account-msg">{sessionStatus}</div> : null}
          </div>
        ) : null}
      </div>
      <button type="button" className="erp-iconbtn" onClick={() => void logout()}>
        Sign out
      </button>
    </header>
  )
}
