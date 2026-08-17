import { useSettings } from './SettingsProvider'
import { ManualSectionedView } from './markdownComponents'
import { formatRelativeTime } from './helpers'

export type CompanyProfilePanelProps = {
  variant?: 'modal' | 'setup'
  onClose?: () => void
  onOpenKnowledge?: () => void
}

export function CompanyProfilePanel({ onClose }: CompanyProfilePanelProps) {
  const s = useSettings() as Record<string, any>
  const {
    companies, activeCompany, switchCompany,
    workspaceErr, setWorkspaceErr,
    workspaceSearch, setWorkspaceSearch,
    workspaceAddName, setWorkspaceAddName,
    workspaceAddBusy, addWorkspace,
    activeIsOwner, filteredWorkspaces,
    deleteWorkspace, setDeleteWorkspace,
    deleteWorkspaceConfirm, setDeleteWorkspaceConfirm,
    deleteWorkspaceBusy, confirmDeleteWorkspace,
    manualLoading, manualError, manualContent, manualVersion, manualUpdatedAt,
    manualEditMode, setManualEditMode, manualDraft, setManualDraft,
    manualSaveStatus, handleSaveManual, startEditCompanyKnowledge,
    contextRule, classificationLoading,
    onOpenWizard, wizardCompleted,
  } = s

  const knowledgePreview = ((contextRule?.content as string | undefined) || manualContent || '').trim()
  const knowledgeLoading = manualLoading || classificationLoading
  const knowledgeSaving = manualSaveStatus === 'saving'

  return (
    <>
          <div className="company-tab manus-rules-surface">
            <div className="skills-surface-header">
              <h3 className="manus-page-title">Company</h3>
              <p className="manus-page-subtitle">Workspaces and company knowledge used by AI for this workspace.</p>
            </div>

            <div className="settings-company-section">
              <h4 className="rules-section-title">Workspaces</h4>
              <p className="settings-company-lead">Companies you can open. Switch the active workspace or add another.</p>
              {workspaceErr && (
                <div className="settings-company-error" role="alert">
                  {workspaceErr}
                </div>
              )}
              <h4 className="settings-company-subheading">Your workspaces</h4>
              <div className="knowledge-toolbar manus-knowledge-toolbar settings-company-workspace-toolbar">
                <input
                  type="search"
                  className="knowledge-search"
                  placeholder="Search workspaces"
                  value={workspaceSearch}
                  onChange={e => setWorkspaceSearch(e.target.value)}
                  aria-label="Search workspaces"
                />
                <div className="knowledge-toolbar-actions">
                  {workspaceSearch.trim() && (
                    <button
                      type="button"
                      className="manus-btn manus-btn-outline"
                      onClick={() => setWorkspaceSearch('')}
                    >
                      Clear
                    </button>
                  )}
                </div>
              </div>
              {activeIsOwner && (
                <div className="skills-custom-banner settings-company-workspace-banner">
                  <div>
                    <div className="skills-custom-banner-title">Add a workspace</div>
                    <p className="skills-custom-banner-desc">You can switch between workspaces from this list or the header.</p>
                  </div>
                  <form
                    className="settings-workspace-add-form"
                    onSubmit={e => {
                      e.preventDefault()
                      void addWorkspace()
                    }}
                  >
                    <input
                      className="settings-input"
                      value={workspaceAddName}
                      onChange={e => setWorkspaceAddName(e.target.value)}
                      placeholder="New workspace name"
                    />
                    <button
                      type="submit"
                      className="manus-btn manus-btn-primary"
                      disabled={workspaceAddBusy || !workspaceAddName.trim()}
                    >
                      {workspaceAddBusy ? 'Adding…' : 'Add workspace'}
                    </button>
                  </form>
                </div>
              )}
              {companies.length === 0 ? (
                <p className="settings-company-hint">No workspaces in your account yet.</p>
              ) : filteredWorkspaces.length === 0 && workspaceSearch.trim() ? (
                <div className="knowledge-empty">No workspaces match this search. Clear the filter or add a new workspace above.</div>
              ) : (
                <div className="knowledge-list manus-knowledge-list settings-company-workspace-list">
                  {filteredWorkspaces.map(c => (
                    <div key={c.id} className="knowledge-card knowledge-card--article">
                      <div className="knowledge-card-main">
                        <div className="knowledge-card-title-row">
                          <span className="knowledge-card-title">{c.name}</span>
                          {c.id === activeCompany?.id && (
                            <span className="knowledge-badge knowledge-badge--context">Active</span>
                          )}
                        </div>
                        <p className="knowledge-card-desc">
                          {c.roleLabel}
                        </p>
                        <div className="knowledge-card-foot">
                          <span className="knowledge-card-date">
                            {c.id === activeCompany?.id ? 'In use' : 'Available'}
                          </span>
                          <div className="knowledge-card-actions">
                            {c.id !== activeCompany?.id && (
                              <button
                                type="button"
                                className="manus-btn manus-btn-outline"
                                onClick={() => { setWorkspaceErr(''); switchCompany(c.id) }}
                              >
                                Switch
                              </button>
                            )}
                            {c.role === 'owner' && companies.length > 1 && (
                              <button
                                type="button"
                                className="manus-btn manus-btn-danger-outline"
                                onClick={() => {
                                  setWorkspaceErr('')
                                  setDeleteWorkspaceConfirm('')
                                  setDeleteWorkspace({ id: c.id, name: c.name })
                                }}
                              >
                                Delete
                              </button>
                            )}
                          </div>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
              {activeIsOwner && companies.length === 1 && (
                <p className="settings-company-hint">Create another workspace before you can delete the current one.</p>
              )}
            </div>

            <div className="settings-company-section settings-company-section--divided">
              <h4 className="rules-section-title">Company setup</h4>
              <p className="settings-company-lead">
                Edit company knowledge here. The setup wizard can regenerate profile fields and this narrative together.
              </p>
              {manualError && (
                <div className="settings-company-error" role="alert">
                  {manualError}
                </div>
              )}
              {knowledgeLoading && !manualEditMode ? (
                <p className="settings-company-hint">Loading company knowledge…</p>
              ) : manualEditMode ? (
                <div className="company-manual-summary">
                  <p className="company-manual-meta">
                    <strong>Company knowledge</strong>
                    {' · editing'}
                  </p>
                  <textarea
                    className="settings-input manus-textarea company-knowledge-editor"
                    rows={14}
                    value={manualDraft}
                    onChange={e => setManualDraft(e.target.value)}
                    placeholder="Company profile, business narrative, clients, vendors, and notes the AI should know…"
                    disabled={knowledgeSaving}
                  />
                </div>
              ) : knowledgePreview ? (
                <div className="company-manual-summary">
                  <p className="company-manual-meta">
                    <strong>Company knowledge</strong>
                    {` · version ${manualVersion}`}
                    {manualUpdatedAt ? ` · updated ${formatRelativeTime(manualUpdatedAt)}` : ''}
                  </p>
                  <div className="company-manual-preview">
                    <ManualSectionedView content={knowledgePreview} />
                  </div>
                </div>
              ) : (
                <p className="settings-company-hint">No company knowledge yet. Edit below to add it, or run the setup wizard.</p>
              )}
              <div className="company-tab-actions">
                {manualEditMode ? (
                  <>
                    <button
                      type="button"
                      className="manus-btn manus-btn-outline"
                      onClick={() => {
                        setManualEditMode(false)
                        setManualDraft('')
                      }}
                      disabled={knowledgeSaving}
                    >
                      Cancel
                    </button>
                    <button
                      type="button"
                      className="manus-btn manus-btn-primary"
                      onClick={() => void handleSaveManual()}
                      disabled={knowledgeSaving || !manualDraft.trim()}
                    >
                      {knowledgeSaving ? 'Saving…' : 'Save'}
                    </button>
                  </>
                ) : (
                  <>
                    {onOpenWizard && !wizardCompleted && (
                      <button
                        type="button"
                        className="manus-btn manus-btn-outline"
                        onClick={() => { onClose?.(); onOpenWizard() }}
                      >
                        Setup wizard
                      </button>
                    )}
                    <button
                      type="button"
                      className="manus-btn manus-btn-primary"
                      onClick={() => startEditCompanyKnowledge(knowledgePreview)}
                      disabled={knowledgeLoading}
                    >
                      {knowledgePreview ? 'Edit' : 'Add company knowledge'}
                    </button>
                  </>
                )}
              </div>
            </div>
          </div>

        {deleteWorkspace && (
          <div
            className="settings-nested-overlay"
            role="presentation"
            onClick={() => {
              if (!deleteWorkspaceBusy) {
                setDeleteWorkspace(null)
                setDeleteWorkspaceConfirm('')
              }
            }}
          >
            <div className="settings-form-modal" onClick={e => e.stopPropagation()}>
              <h4 className="settings-form-modal-title">Delete workspace</h4>
              <p className="settings-description" style={{ marginBottom: 12 }}>
                This will permanently delete <strong>{deleteWorkspace.name}</strong> and all of its data. This cannot
                be undone.
              </p>
              <label className="settings-form-label">Type the workspace name to confirm</label>
              <input
                className="settings-input"
                value={deleteWorkspaceConfirm}
                onChange={e => setDeleteWorkspaceConfirm(e.target.value)}
                autoComplete="off"
              />
              <div className="settings-form-modal-actions" style={{ marginTop: 16 }}>
                <button
                  type="button"
                  className="memory-btn"
                  onClick={() => {
                    if (!deleteWorkspaceBusy) {
                      setDeleteWorkspace(null)
                      setDeleteWorkspaceConfirm('')
                    }
                  }}
                  disabled={deleteWorkspaceBusy}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="memory-btn primary"
                  onClick={() => void confirmDeleteWorkspace()}
                  disabled={
                    deleteWorkspaceBusy || deleteWorkspaceConfirm.trim() !== deleteWorkspace.name.trim()
                  }
                >
                  {deleteWorkspaceBusy ? 'Deleting…' : 'Delete permanently'}
                </button>
              </div>
            </div>
          </div>
        )}

    </>
  )
}
