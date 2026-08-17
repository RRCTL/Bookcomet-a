import { useState, useEffect } from 'react'
import { useAuth } from '../../contexts/AuthContext'
import '../../components/Settings.css'
import { SettingsProvider, useSettings } from '../../components/settings/SettingsProvider'
import { CompanyProfilePanel } from '../../components/settings/CompanyProfilePanel'
import { CoAPanel } from '../../components/settings/CoAPanel'
import { SkillsMemoryPanel } from '../../components/settings/SkillsMemoryPanel'
import { ApiSettingsPanel } from '../../components/settings/ApiSettingsPanel'
import { OnboardingWizard } from '../../components/OnboardingWizard'
import { useCompanyOnboardingWizard } from '../../hooks/useCompanyOnboardingWizard'

type SetupTab = 'company' | 'accounts' | 'rules' | 'api'

const TAB_LABELS: Record<SetupTab, 'Company Profile' | 'CoA' | 'Skills Memory' | 'API'> = {
  company: 'Company Profile',
  accounts: 'CoA',
  rules: 'Skills Memory',
  api: 'API',
}

function setupTabKey(companyId: string) {
  return `erp.setup.tab.${companyId}`
}

function SetupWizardHost({
  showWizard,
  onCloseWizard,
}: {
  showWizard: boolean
  onCloseWizard: () => void
}) {
  const {
    setWizardCompleted,
    loadCompanyManual,
    loadClassificationRules,
    reloadCoA,
  } = useSettings() as {
    setWizardCompleted: (value: boolean) => void
    loadCompanyManual: () => Promise<void>
    loadClassificationRules: () => Promise<void>
    reloadCoA: () => Promise<void>
  }

  if (!showWizard) return null

  return (
    <OnboardingWizard
      onComplete={() => {
        onCloseWizard()
        setWizardCompleted(true)
        void loadCompanyManual()
        void loadClassificationRules()
        void reloadCoA()
      }}
      onSkip={onCloseWizard}
    />
  )
}

function SetupPageBody({
  showWizard,
  onCloseWizard,
}: {
  showWizard: boolean
  onCloseWizard: () => void
}) {
  const { activeCompany } = useAuth()
  const companyId = activeCompany?.id ?? 'default'
  const [activeTab, setActiveTab] = useState<SetupTab>('company')

  useEffect(() => {
    const stored = localStorage.getItem(setupTabKey(companyId))
    if (stored === 'company' || stored === 'accounts' || stored === 'rules' || stored === 'api') {
      setActiveTab(stored)
    }
  }, [companyId])

  const selectTab = (tab: SetupTab) => {
    setActiveTab(tab)
    localStorage.setItem(setupTabKey(companyId), tab)
  }

  return (
    <>
      <div className="erp-setup-tabs" role="tablist" aria-label="Setup sections">
        {(Object.keys(TAB_LABELS) as SetupTab[]).map(tab => (
          <button
            key={tab}
            type="button"
            role="tab"
            aria-selected={activeTab === tab}
            className={`erp-setup-tab ${activeTab === tab ? 'active' : ''}`}
            onClick={() => selectTab(tab)}
          >
            {TAB_LABELS[tab]}
          </button>
        ))}
      </div>
      <div className="erp-setup-body">
        {activeTab === 'company' && <CompanyProfilePanel variant="setup" />}
        {activeTab === 'accounts' && <CoAPanel />}
        {activeTab === 'rules' && (
          <div className="memory-tab rules-tab-v2 manus-rules-surface">
            <SkillsMemoryPanel />
          </div>
        )}
        {activeTab === 'api' && <ApiSettingsPanel />}
      </div>
      <SetupWizardHost showWizard={showWizard} onCloseWizard={onCloseWizard} />
    </>
  )
}

export function SetupPage() {
  const { user, activeCompany } = useAuth()
  const { showWizard, setShowWizard } = useCompanyOnboardingWizard(!!user, activeCompany?.id)

  return (
    <SettingsProvider
      enabled
      allTransactions={[]}
      onOpenWizard={() => setShowWizard(true)}
    >
      <SetupPageBody showWizard={showWizard} onCloseWizard={() => setShowWizard(false)} />
    </SettingsProvider>
  )
}
