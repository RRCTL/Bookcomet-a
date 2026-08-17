import { useSettings } from './SettingsProvider'
import { KNOWLEDGE_NOTE_MAX, formatRelativeTime } from './helpers'
import type { ClassificationRuleApiRow } from './types'

export function KnowledgePanel() {
  const {
    knowledgeSearch, setKnowledgeSearch,
    setCrEditingId, setCrModalKind, setCrForm, setCrModalOpen,
    setExclEditingId, setExclForm, setExclModalOpen,
    classificationError, exclusionError, setClassificationError, setExclusionError,
    classificationLoading, exclusionLoading, knowledgeRows, contextRule, contextMatchesSearch,
    formatKnowledgeDate, toggleClassificationRule, setContextDraft, setContextModalOpen,
    knowledgeMenuId, setKnowledgeMenuId, deleteClassificationRule,
    crModalOpen, crModalKind, crForm, crEditingId, crSaving, saveCrModal,
    contextModalOpen, contextDraft, contextSaving, saveContextModal,
    exclModalOpen, exclForm, exclEditingId, exclSaving, saveExclModal,
    classificationRules, exclusionRules, handleToggleExclusion, handleDeleteExclusion,
    manualContent,
  } = useSettings() as Record<string, any>

  const openCreateContext = () => {
    setContextDraft({ use_when: '', body: (manualContent || '').trim() })
    setContextModalOpen(true)
  }

  return (
    <>
            <div className="knowledge-surface-header">
              <h3 className="manus-page-title">Knowledge</h3>
              <p className="manus-page-subtitle">Manage who you are and what the AI remembers — business context, notes, classification hints, and block rules.</p>
            </div>

            <div className="knowledge-toolbar manus-knowledge-toolbar">
              <input
                type="search"
                className="knowledge-search"
                placeholder="Search Knowledge"
                value={knowledgeSearch}
                onChange={e => setKnowledgeSearch(e.target.value)}
              />
              <div className="knowledge-toolbar-actions">
                <button
                  type="button"
                  className="manus-btn manus-btn-primary"
                  onClick={() => {
                    setCrEditingId(null)
                    setCrModalKind('knowledge')
                    setCrForm({
                      rule_name: '',
                      pattern_type: 'keyword',
                      pattern: '',
                      notes: '',
                      document_type: '',
                      use_when: '',
                      content: '',
                    })
                    setCrModalOpen(true)
                  }}
                >
                  + Add
                </button>
                <button
                  type="button"
                  className="manus-btn manus-btn-outline"
                  onClick={() => {
                    setCrEditingId(null)
                    setCrModalKind('classification')
                    setCrForm({
                      rule_name: '',
                      pattern_type: 'keyword',
                      pattern: '',
                      notes: '',
                      document_type: '',
                      use_when: '',
                      content: '',
                    })
                    setCrModalOpen(true)
                  }}
                >
                  + Classification
                </button>
                <button
                  type="button"
                  className="manus-btn manus-btn-outline"
                  onClick={() => {
                    setExclEditingId(null)
                    setExclForm({ pattern: '', pattern_type: 'keyword', reason: '', modes: '' })
                    setExclModalOpen(true)
                  }}
                >
                  + Block
                </button>
              </div>
            </div>

            {(classificationError || exclusionError) && (
              <div className="memory-error-banner" style={{ marginTop: 8 }}>
                {classificationError || exclusionError}
                <button type="button" onClick={() => { setClassificationError(''); setExclusionError('') }}>×</button>
              </div>
            )}

            {(classificationLoading || exclusionLoading) && knowledgeRows.length === 0 && !contextRule ? (
              <div className="knowledge-loading">Loading…</div>
            ) : !contextMatchesSearch && knowledgeRows.length === 0 ? (
              <div className="knowledge-empty">No knowledge items match. Add or clear the search.</div>
            ) : (
              <div className="knowledge-list manus-knowledge-list">
                {contextMatchesSearch && contextRule && (
                  <div className="knowledge-card knowledge-card--context">
                    <div className="knowledge-card-main">
                      <div className="knowledge-card-title-row">
                        <span className="knowledge-card-title">{contextRule.rule_name}</span>
                        <span className="knowledge-badge knowledge-badge--context">CONTEXT</span>
                      </div>
                      <p className="knowledge-card-desc">
                        {(contextRule.content || '').length > 200
                          ? `${(contextRule.content || '').slice(0, 199)}…`
                          : (contextRule.content || 'No business context yet. Edit to add profile and narrative for AI chat.')}
                      </p>
                      <div className="knowledge-card-foot">
                        <span className="knowledge-card-date">
                          {contextRule.created_at ? `Created ${formatKnowledgeDate(contextRule.created_at)}` : ''}
                        </span>
                        <div className="knowledge-card-actions">
                          <button
                            type="button"
                            className={`knowledge-toggle ${contextRule.is_active ? 'on' : ''}`}
                            onClick={() => void toggleClassificationRule(contextRule)}
                            aria-label={contextRule.is_active ? 'Disable' : 'Enable'}
                          />
                          <div className="knowledge-menu-wrap">
                            <button
                              type="button"
                              className="knowledge-menu-btn"
                              onClick={() => {
                                setContextDraft({
                                  use_when: contextRule.use_when || '',
                                  body: contextRule.content || '',
                                })
                                setContextModalOpen(true)
                              }}
                            >
                              Edit
                            </button>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                )}
                {contextMatchesSearch && !contextRule && !knowledgeSearch.trim() && (
                  <div className="knowledge-card knowledge-card--context muted">
                    <div className="knowledge-card-main">
                      <div className="knowledge-card-title-row">Business context</div>
                      <p className="knowledge-card-desc">No context row yet. Run the setup wizard or edit here to create one.</p>
                      <div className="knowledge-card-foot">
                        <button
                          type="button"
                          className="manus-btn manus-btn-outline"
                          onClick={openCreateContext}
                        >
                          Create business context
                        </button>
                      </div>
                    </div>
                  </div>
                )}
                {knowledgeRows.map(row => {
                  if (row.kind === 'classification') {
                    const r = row.rule
                    const isGate = r.rule_type === 'document_gate'
                    const isK = r.rule_type === 'knowledge_article'
                    const menuOpen = knowledgeMenuId === row.id
                    const desc = isK
                      ? [r.use_when && `${r.use_when}`, r.content && (r.content.length > 180 ? `${r.content.slice(0, 179)}…` : r.content)]
                        .filter(Boolean)
                        .join(' — ') || '—'
                      : `${r.pattern_type}: ${r.pattern}${r.notes ? ` · ${r.notes}` : ''}`
                    return (
                      <div key={row.id} className={`knowledge-card ${isGate ? 'knowledge-card--gate' : ''} ${isK ? 'knowledge-card--article' : ''}`}>
                        <div className="knowledge-card-main">
                          <div className="knowledge-card-title-row">
                            <span className="knowledge-card-title">{r.rule_name}</span>
                            {isGate && <span className="knowledge-badge knowledge-badge--gate">GATE</span>}
                            {isK && <span className="knowledge-badge knowledge-badge--article">NOTE</span>}
                            {r.document_type && <span className="knowledge-badge">{r.document_type}</span>}
                          </div>
                          <p className="knowledge-card-desc">{desc}</p>
                          <div className="knowledge-card-foot">
                            <span className="knowledge-card-date">
                              {r.created_at ? `Created ${formatKnowledgeDate(r.created_at)}` : ''}
                              {r.hit_count > 0 ? ` · ${r.hit_count} hits` : ''}
                            </span>
                            <div className="knowledge-card-actions">
                              <button
                                type="button"
                                className={`knowledge-toggle ${r.is_active ? 'on' : ''}`}
                                onClick={() => void toggleClassificationRule(r)}
                                aria-label={r.is_active ? 'Disable' : 'Enable'}
                              />
                              <div className="knowledge-menu-wrap">
                                <button type="button" className="knowledge-menu-btn" onClick={() => setKnowledgeMenuId(menuOpen ? null : row.id)}>⋯</button>
                                {menuOpen && (
                                  <div className="knowledge-menu-dropdown">
                                    <button
                                      type="button"
                                      className="knowledge-menu-item"
                                      onClick={() => {
                                        setKnowledgeMenuId(null)
                                        setCrEditingId(r.id)
                                        if (isK) {
                                          setCrModalKind('knowledge')
                                          setCrForm({
                                            rule_name: r.rule_name,
                                            pattern_type: 'keyword',
                                            pattern: '',
                                            notes: '',
                                            document_type: '',
                                            use_when: r.use_when || '',
                                            content: r.content || '',
                                          })
                                        } else {
                                          setCrModalKind('classification')
                                          setCrForm({
                                            rule_name: r.rule_name,
                                            pattern_type: (r.pattern_type === 'vendor' || r.pattern_type === 'amount' ? r.pattern_type : 'keyword'),
                                            pattern: r.pattern || '',
                                            notes: r.notes || '',
                                            document_type: r.document_type || '',
                                            use_when: '',
                                            content: '',
                                          })
                                        }
                                        setCrModalOpen(true)
                                      }}
                                    >
                                      Edit
                                    </button>
                                    {!isGate && (
                                      <button
                                        type="button"
                                        className="knowledge-menu-item danger"
                                        onClick={() => {
                                          setKnowledgeMenuId(null)
                                          void (async () => {
                                            await deleteClassificationRule(r.id)
                                          })()
                                        }}
                                      >
                                        Delete
                                      </button>
                                    )}
                                  </div>
                                )}
                              </div>
                            </div>
                          </div>
                        </div>
                      </div>
                    )
                  }
                  const r = row.rule
                  const menuOpen = knowledgeMenuId === row.id
                  return (
                    <div key={row.id} className="knowledge-card knowledge-card--block">
                      <div className="knowledge-card-main">
                        <div className="knowledge-card-title-row">
                          <span className="knowledge-badge knowledge-badge--block">BLOCK</span>
                          <span className="knowledge-card-title">
                            {r.pattern_type === 'amount' ? `Amount ≥ ${r.pattern}` : r.pattern}
                          </span>
                          <span className={`exclusion-type-badge type-${r.pattern_type}`} style={{ marginLeft: 6 }}>{r.pattern_type}</span>
                        </div>
                        {r.reason && <p className="knowledge-card-desc">{r.reason}</p>}
                        {r.modes && <p className="knowledge-card-desc" style={{ fontSize: 11 }}>Modes: {r.modes}</p>}
                        <div className="knowledge-card-foot">
                          <span className="knowledge-card-date">
                            {r.hit_count > 0 ? `${r.hit_count} hits` : ''}
                          </span>
                          <div className="knowledge-card-actions">
                            <button
                              type="button"
                              className={`knowledge-toggle ${r.is_active ? 'on' : ''}`}
                              onClick={() => void handleToggleExclusion(r)}
                            />
                            <div className="knowledge-menu-wrap">
                              <button type="button" className="knowledge-menu-btn" onClick={() => setKnowledgeMenuId(menuOpen ? null : row.id)}>⋯</button>
                              {menuOpen && (
                                <div className="knowledge-menu-dropdown">
                                  <button
                                    type="button"
                                    className="knowledge-menu-item"
                                    onClick={() => {
                                      setKnowledgeMenuId(null)
                                      setExclEditingId(r.id)
                                      setExclForm({
                                        pattern: r.pattern,
                                        pattern_type: r.pattern_type,
                                        reason: r.reason || '',
                                        modes: r.modes || '',
                                      })
                                      setExclModalOpen(true)
                                    }}
                                  >
                                    Edit
                                  </button>
                                  <button
                                    type="button"
                                    className="knowledge-menu-item danger"
                                    onClick={() => {
                                      setKnowledgeMenuId(null)
                                      void handleDeleteExclusion(r.id)
                                    }}
                                  >
                                    Delete
                                  </button>
                                </div>
                              )}
                            </div>
                          </div>
                        </div>
                      </div>
                    </div>
                  )
                })}
              </div>
            )}

            {crModalOpen && (
              <div className="settings-nested-overlay manus-overlay" role="presentation" onClick={() => !crSaving && setCrModalOpen(false)}>
                <div className="settings-form-modal manus-edit-modal" onClick={e => e.stopPropagation()}>
                  <button type="button" className="manus-modal-close" onClick={() => setCrModalOpen(false)} aria-label="Close">×</button>
                  <h4 className="settings-form-modal-title">
                    {crModalKind === 'knowledge' ? (crEditingId ? 'Edit Knowledge' : 'Add Knowledge') : (crEditingId ? 'Edit classification' : 'Add classification')}
                  </h4>
                  <label className="settings-form-label">Name</label>
                  <input className="settings-input manus-input" value={crForm.rule_name} onChange={e => setCrForm(f => ({ ...f, rule_name: e.target.value }))} />
                  {crModalKind === 'knowledge' ? (
                    <>
                      <label className="settings-form-label">Use when</label>
                      <input
                        className="settings-input manus-input"
                        value={crForm.use_when}
                        onChange={e => setCrForm(f => ({ ...f, use_when: e.target.value }))}
                        placeholder="When should the AI apply this?"
                      />
                      <label className="settings-form-label">Content</label>
                      <textarea
                        className="settings-input manus-textarea"
                        rows={8}
                        value={crForm.content}
                        onChange={e => setCrForm(f => ({ ...f, content: e.target.value }))}
                      />
                      <div className="manus-char-count">{crForm.content.length} / {KNOWLEDGE_NOTE_MAX}</div>
                    </>
                  ) : (
                    <>
                      <label className="settings-form-label">Pattern type</label>
                      <select
                        className="settings-input manus-input"
                        value={crForm.pattern_type}
                        onChange={e => setCrForm(f => ({ ...f, pattern_type: e.target.value as 'keyword' | 'vendor' | 'amount' }))}
                      >
                        <option value="keyword">Keyword</option>
                        <option value="vendor">Vendor / name</option>
                        <option value="amount">Amount threshold</option>
                      </select>
                      <label className="settings-form-label">Pattern</label>
                      <input
                        className="settings-input manus-input"
                        type={crForm.pattern_type === 'amount' ? 'number' : 'text'}
                        value={crForm.pattern}
                        onChange={e => setCrForm(f => ({ ...f, pattern: e.target.value }))}
                      />
                      <label className="settings-form-label">Action / notes</label>
                      <textarea className="settings-input manus-textarea" rows={3} value={crForm.notes} onChange={e => setCrForm(f => ({ ...f, notes: e.target.value }))} />
                      <label className="settings-form-label">Mode filter (optional)</label>
                      <select
                        className="settings-input manus-input"
                        value={crForm.document_type}
                        onChange={e => setCrForm(f => ({ ...f, document_type: e.target.value }))}
                      >
                        <option value="">All modes</option>
                        <option value="AR">AR</option>
                        <option value="AP">AP</option>
                        <option value="BANK">BANK</option>
                        <option value="OTHER">Other</option>
                      </select>
                    </>
                  )}
                  <div className="settings-form-modal-actions manus-modal-footer">
                    {crEditingId && crModalKind === 'knowledge' && (
                      <button
                        type="button"
                        className="manus-btn manus-btn-danger-outline"
                        onClick={() => {
                          void (async () => {
                            const ok = await deleteClassificationRule(crEditingId)
                            if (ok) setCrModalOpen(false)
                          })()
                        }}
                      >
                        Delete
                      </button>
                    )}
                    {crEditingId && crModalKind === 'classification' && (
                      <button
                        type="button"
                        className="manus-btn manus-btn-danger-outline"
                        onClick={() => {
                          void (async () => {
                            const ok = await deleteClassificationRule(crEditingId)
                            if (ok) setCrModalOpen(false)
                          })()
                        }}
                      >
                        Delete
                      </button>
                    )}
                    <button type="button" className="manus-btn manus-btn-outline" onClick={() => setCrModalOpen(false)} disabled={crSaving}>Cancel</button>
                    <button
                      type="button"
                      className="manus-btn manus-btn-primary"
                      onClick={() => void saveCrModal()}
                      disabled={
                        crSaving ||
                        !crForm.rule_name.trim() ||
                        (crModalKind === 'knowledge'
                          ? !crForm.content.trim() || crForm.content.length > KNOWLEDGE_NOTE_MAX
                          : !crForm.pattern.trim())
                      }
                    >
                      {crSaving ? 'Saving…' : 'Save'}
                    </button>
                  </div>
                </div>
              </div>
            )}

            {contextModalOpen && (
              <div className="settings-nested-overlay manus-overlay" role="presentation" onClick={() => !contextSaving && setContextModalOpen(false)}>
                <div className="settings-form-modal manus-edit-modal" onClick={e => e.stopPropagation()}>
                  <button type="button" className="manus-modal-close" onClick={() => setContextModalOpen(false)} aria-label="Close">×</button>
                  <h4 className="settings-form-modal-title">Business context</h4>
                  <p className="settings-description" style={{ fontSize: 13, marginTop: 0 }}>Used for AI chat and reconciliation prompts. Include profile summary and operating narrative.</p>
                  <label className="settings-form-label">Use when (optional)</label>
                  <input
                    className="settings-input manus-input"
                    value={contextDraft.use_when}
                    onChange={e => setContextDraft(d => ({ ...d, use_when: e.target.value }))}
                  />
                  <label className="settings-form-label">Content</label>
                  <textarea
                    className="settings-input manus-textarea"
                    rows={12}
                    value={contextDraft.body}
                    onChange={e => setContextDraft(d => ({ ...d, body: e.target.value }))}
                  />
                  <div className="settings-form-modal-actions manus-modal-footer">
                    <button type="button" className="manus-btn manus-btn-outline" onClick={() => setContextModalOpen(false)} disabled={contextSaving}>Cancel</button>
                    <button
                      type="button"
                      className="manus-btn manus-btn-primary"
                      onClick={() => void saveContextModal()}
                      disabled={contextSaving || !contextDraft.body.trim()}
                    >
                      {contextSaving ? 'Saving…' : 'Save'}
                    </button>
                  </div>
                </div>
              </div>
            )}

            {exclModalOpen && (
              <div className="settings-nested-overlay" role="presentation" onClick={() => !exclSaving && setExclModalOpen(false)}>
                <div className="settings-form-modal" onClick={e => e.stopPropagation()}>
                  <h4 className="settings-form-modal-title">{exclEditingId ? 'Edit block rule' : 'Add block rule'}</h4>
                  <label className="settings-form-label">Pattern type</label>
                  <select
                    className="settings-input"
                    value={exclForm.pattern_type}
                    onChange={e => setExclForm(f => ({ ...f, pattern_type: e.target.value }))}
                  >
                    <option value="keyword">Keyword</option>
                    <option value="vendor">Vendor / name</option>
                    <option value="amount">Amount ≥</option>
                  </select>
                  <label className="settings-form-label">Pattern</label>
                  <input
                    className="settings-input"
                    type={exclForm.pattern_type === 'amount' ? 'number' : 'text'}
                    value={exclForm.pattern}
                    onChange={e => setExclForm(f => ({ ...f, pattern: e.target.value }))}
                  />
                  <label className="settings-form-label">Reason (optional)</label>
                  <input className="settings-input" value={exclForm.reason} onChange={e => setExclForm(f => ({ ...f, reason: e.target.value }))} />
                  <label className="settings-form-label">Modes (optional, e.g. AR,AP)</label>
                  <input className="settings-input" value={exclForm.modes} onChange={e => setExclForm(f => ({ ...f, modes: e.target.value }))} />
                  <div className="settings-form-modal-actions">
                    <button type="button" className="memory-btn" onClick={() => setExclModalOpen(false)} disabled={exclSaving}>Cancel</button>
                    <button
                      type="button"
                      className="memory-btn primary"
                      onClick={() => void saveExclModal()}
                      disabled={exclSaving || !exclForm.pattern.trim()}
                    >
                      {exclSaving ? 'Saving…' : 'Save'}
                    </button>
                  </div>
                </div>
              </div>
            )}

    </>
  )
}
