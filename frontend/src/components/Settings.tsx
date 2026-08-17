import { useState, useEffect } from 'react'
import './Settings.css'
import { ruleMemorySeenKey } from './settings/helpers'
import { SettingsProvider, useSettings } from './settings/SettingsProvider'
import { CompanyProfilePanel } from './settings/CompanyProfilePanel'
import { CoAPanel } from './settings/CoAPanel'
import { SkillsMemoryPanel } from './settings/SkillsMemoryPanel'
import { KnowledgePanel } from './settings/KnowledgePanel'
import { ApiSettingsPanel } from './settings/ApiSettingsPanel'

export { ruleMemorySeenKey }

type SettingsTab = 'company' | 'accounts' | 'rules' | 'api'

interface SettingsProps {
  isOpen: boolean
  onClose: () => void
  allTransactions?: Array<{ account_code?: string; id_number?: string; date?: string; amount?: number | null; transaction_type?: string }>
  onOpenChatWithMode?: (mode: string) => void
  openToMemoryTab?: boolean
  onOpenWizard?: () => void
}

function SettingsModalBody({
  activeTab,
  setActiveTab,
  onClose,
  openToMemoryTab,
  onOpenWizard,
}: {
  activeTab: SettingsTab
  setActiveTab: (tab: SettingsTab) => void
  onClose: () => void
  openToMemoryTab?: boolean
  onOpenWizard?: () => void
}) {
  const {
    loadAllSummaries,
    loadExclusionRules,
    loadClassificationRules,
  } = useSettings() as {
    loadAllSummaries: () => Promise<void>
    loadExclusionRules: () => Promise<void>
    loadClassificationRules: () => Promise<void>
  }

  useEffect(() => {
    if (openToMemoryTab) {
      setActiveTab('rules')
      void loadAllSummaries()
      void loadExclusionRules()
      void loadClassificationRules()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openToMemoryTab])

  return (
    <div className="settings-overlay" onClick={onClose}>
      <div className="settings-modal" onClick={e => e.stopPropagation()}>
        <div className="settings-header">
          <h2>Company setting</h2>
          <button className="settings-close" onClick={onClose} type="button">×</button>
        </div>

        <div className="settings-content">
          <div className="settings-tabs">
            <button
              className={`settings-tab ${activeTab === 'company' ? 'active' : ''}`}
              onClick={() => setActiveTab('company')}
              type="button"
            >
              Company
            </button>
            <button
              className={`settings-tab ${activeTab === 'accounts' ? 'active' : ''}`}
              onClick={() => setActiveTab('accounts')}
              type="button"
            >
              Accounts
            </button>
            <button
              className={`settings-tab ${activeTab === 'rules' ? 'active' : ''}`}
              onClick={() => {
                setActiveTab('rules')
                void loadAllSummaries()
                void loadExclusionRules()
                void loadClassificationRules()
              }}
              type="button"
            >
              Rules
            </button>
            <button
              className={`settings-tab ${activeTab === 'api' ? 'active' : ''}`}
              onClick={() => setActiveTab('api')}
              type="button"
            >
              API
            </button>
          </div>

          {activeTab === 'company' && (
            <CompanyProfilePanel
              variant="modal"
              onClose={onClose}
              onOpenKnowledge={() => {
                setActiveTab('rules')
                void loadClassificationRules().catch(console.error)
                void loadExclusionRules().catch(console.error)
              }}
            />
          )}

          {activeTab === 'accounts' && <CoAPanel />}

          {activeTab === 'rules' && (
            <div className="memory-tab rules-tab-v2 manus-rules-surface">
              <SkillsMemoryPanel onClose={onClose} />
              <KnowledgePanel />
            </div>
          )}

          {activeTab === 'api' && <ApiSettingsPanel />}
        </div>
      </div>
    </div>
  )
}

export function Settings({
  isOpen,
  onClose,
  allTransactions = [],
  onOpenChatWithMode,
  openToMemoryTab,
  onOpenWizard,
}: SettingsProps) {
  const [activeTab, setActiveTab] = useState<SettingsTab>('company')

  if (!isOpen) return null

  return (
    <SettingsProvider
      enabled={isOpen}
      allTransactions={allTransactions}
      onOpenWizard={onOpenWizard}
      onOpenChatWithMode={onOpenChatWithMode}
    >
      <SettingsModalBody
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        onClose={onClose}
        openToMemoryTab={openToMemoryTab}
        onOpenWizard={onOpenWizard}
      />
    </SettingsProvider>
  )
}
