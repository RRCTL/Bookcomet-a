import { useSettings } from './SettingsProvider'
import {
  RULE_MEMORY_MODES,
  RULE_MEMORY_MODE_LABELS,
  SKILL_SLUG,
  countRules,
  extractBehaviourPreview,
  isAINew,
  skillSkillFilename,
  type RuleMemoryMode,
} from './helpers'
import { MarkdownRenderer } from './markdownComponents'

export type SkillsMemoryPanelProps = {
  onClose?: () => void
}

export function SkillsMemoryPanel({ onClose }: SkillsMemoryPanelProps = {}) {
  const {
    activeCompanyId, memorySummaries, allSummariesLoading, skillSearch, setSkillSearch,
    skillGenerateOpen, setSkillGenerateOpen, generateDesc, setGenerateDesc, generating,
    generateMemory, openSkillModal, mdModalOpen, setMdModalOpen, memoryMode,
    mdModalEditing, setMdModalEditing, memoryLoading, memoryContent,
    memoryDraft, setMemoryDraft, isMemoryEditMode, setIsMemoryEditMode,
    memorySaveStatus, memoryError, setMemoryError, skillYamlBlock,
    onOpenChatWithMode, switchMemoryMode, setKnowledgeMenuId,
    visibleSkillModes, formatKnowledgeDate, toggleSkillActive,
    conflictCount, handleSaveMemory, handleRestoreLastVersion,
  } = useSettings() as Record<string, any>

  return (
    <>
            <div className="skills-surface-header">
              <h3 className="manus-page-title">Skills</h3>
              <p className="manus-page-subtitle">Prepackaged processing instructions per mode (SKILL.md). AR / AP / BANK / OTHER drive OCR and chat and onboarding rule-memory export.</p>
            </div>
            <div className="skills-toolbar-row">
              <input
                type="search"
                className="skills-search-input"
                placeholder="Search skills…"
                value={skillSearch}
                onChange={e => setSkillSearch(e.target.value)}
              />
              <span className="skills-official-pill" title="Built-in modes">Official</span>
            </div>
            <div className={`skills-custom-banner ${skillGenerateOpen ? 'open' : ''}`}>
              <div>
                <div className="skills-custom-banner-title">Add custom skill content</div>
                <p className="skills-custom-banner-desc">Generate starter SKILL.md from a short business description for the selected mode.</p>
              </div>
              <button
                type="button"
                className="manus-btn manus-btn-primary"
                onClick={() => setSkillGenerateOpen(o => !o)}
              >
                + Generate
              </button>
            </div>
            {skillGenerateOpen && (
              <div className="skills-generate-inline">
                <span className="skills-generate-label">Target mode</span>
                <select
                  className="exclusion-type-select"
                  value={memoryMode}
                  onChange={e => { switchMemoryMode(e.target.value as RuleMemoryMode); setKnowledgeMenuId(null) }}
                >
                  {RULE_MEMORY_MODES.map(m => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
                <input
                  type="text"
                  className="skills-generate-input"
                  placeholder="Business description…"
                  value={generateDesc}
                  onChange={e => setGenerateDesc(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && void generateMemory()}
                />
                <button
                  type="button"
                  className="manus-btn manus-btn-primary"
                  onClick={() => void generateMemory()}
                  disabled={generating || !generateDesc.trim()}
                >
                  {generating ? 'Generating…' : 'Run'}
                </button>
              </div>
            )}

            <div className="skills-grid manus-skills-grid">
              {visibleSkillModes.map(m => {
                const summary = memorySummaries[m]
                const ruleCount = summary ? countRules(summary.content) : 0
                const newAI = isAINew(activeCompanyId, m, summary)
                const updatedLine = summary?.updated_at
                  ? `Updated on ${formatKnowledgeDate(summary.updated_at)}`
                  : ''
                const preview = summary ? extractBehaviourPreview(summary.content) : ''
                const skillOn = summary?.is_active !== false
                return (
                  <div key={m} className="manus-skill-card" role="presentation">
                    <button type="button" className="skill-card-hit" onClick={() => void openSkillModal(m)}>
                      <div className="skill-card-head">
                        <span className="skill-card-name">{SKILL_SLUG[m]}</span>
                        {newAI && <span className="memory-new-badge">AI ✦</span>}
                      </div>
                      <p className="skill-card-desc">{preview || 'Add instructions under AI Behaviour Instructions in SKILL.md.'}</p>
                      <div className="skill-card-meta">
                        <span className="skill-official-label">Official</span>
                        <span>{allSummariesLoading && !summary ? '…' : `${ruleCount} rules`}</span>
                        {updatedLine && <span className="skill-card-date">{updatedLine}</span>}
                      </div>
                    </button>
                    <button
                      type="button"
                      className={`skill-card-toggle knowledge-toggle ${skillOn ? 'on' : ''}`}
                      onClick={e => void toggleSkillActive(m, e)}
                      aria-label={skillOn ? 'Disable skill' : 'Enable skill'}
                    />
                  </div>
                )
              })}
            </div>


            {mdModalOpen && (
              <div
                className="settings-nested-overlay"
                role="presentation"
                onClick={() => { setMdModalOpen(false); setMdModalEditing(false); setMemoryError(''); setKnowledgeMenuId(null) }}
              >
                <div className="settings-md-modal skill-md-modal" role="dialog" aria-modal="true" onClick={e => e.stopPropagation()}>
                  <div className="settings-md-modal-header">
                    <span className="memory-filename skill-md-title">{skillSkillFilename(memoryMode)}</span>
                    <div className="settings-md-modal-header-actions">
                      {onOpenChatWithMode && (
                        <button
                          type="button"
                          className="manus-btn manus-btn-primary"
                          onClick={() => { setMdModalOpen(false); onClose(); onOpenChatWithMode(memoryMode) }}
                        >
                          Try it out
                        </button>
                      )}
                      <button type="button" className="settings-md-modal-close" onClick={() => { setMdModalOpen(false); setMdModalEditing(false) }} aria-label="Close">×</button>
                    </div>
                  </div>
                  {memoryError && (
                    <div className="memory-error-banner" style={{ margin: '0 16px' }}>
                      {memoryError}
                      <button type="button" onClick={() => setMemoryError('')}>×</button>
                    </div>
                  )}
                  {conflictCount > 0 && (
                    <div className="memory-conflict-banner" style={{ margin: '8px 16px 0' }}>
                      ⚠ {conflictCount} duplicate vendor key{conflictCount > 1 ? 's' : ''} in this file.
                    </div>
                  )}
                  <div className="skill-md-body">
                    <aside className="skill-md-rail">
                      <div className="skill-md-folder-label">{SKILL_SLUG[memoryMode]}</div>
                      <div className="skill-md-file active">SKILL.md</div>
                    </aside>
                    <div className="skill-md-pane">
                      <div className="skill-yaml-toolbar">
                        <span className="yaml-chip">YAML</span>
                        <button
                          type="button"
                          className="yaml-copy-btn"
                          onClick={() => void navigator.clipboard?.writeText(skillYamlBlock(memoryMode)).catch(() => undefined)}
                        >
                          Copy
                        </button>
                      </div>
                      <pre className="md-modal-yaml skill-yaml-block">{skillYamlBlock(memoryMode)}</pre>
                      <div className="md-modal-body skill-md-markdown-wrap">
                        {mdModalEditing ? (
                          <textarea
                            className="memory-editor md-modal-editor"
                            value={memoryDraft}
                            onChange={e => setMemoryDraft(e.target.value)}
                            placeholder={`# ${memoryMode} SKILL.md\n\n## AI Behaviour Instructions\n...`}
                          />
                        ) : (
                          <div className="memory-preview md-modal-preview">
                            {memoryLoading ? (
                              <div className="manual-loading">Loading…</div>
                            ) : (
                              <MarkdownRenderer content={memoryContent} />
                            )}
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                  <div className="settings-md-modal-footer skill-md-footer">
                    <button type="button" className="manus-btn manus-btn-outline" onClick={() => void handleRestoreLastVersion()}>↺ Restore last</button>
                    {mdModalEditing ? (
                      <>
                        <button type="button" className="manus-btn manus-btn-outline" onClick={() => { setMdModalEditing(false); setMemoryDraft(memoryContent) }}>Cancel</button>
                        <button
                          type="button"
                          className={`manus-btn manus-btn-primary ${memorySaveStatus === 'success' ? 'success' : memorySaveStatus === 'error' ? 'error' : ''}`}
                          onClick={() => void handleSaveMemory()}
                          disabled={memorySaveStatus === 'saving'}
                        >
                          {memorySaveStatus === 'saving' ? 'Saving…' : 'Save'}
                        </button>
                      </>
                    ) : (
                      <button type="button" className="manus-btn manus-btn-primary" onClick={() => { setMemoryDraft(memoryContent); setMdModalEditing(true) }}>Edit</button>
                    )}
                  </div>
                </div>
              </div>
            )}

    </>
  )
}
