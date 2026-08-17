import { useEffect, useState } from 'react'
import { api } from '../services/api'

/**
 * Auto-opens the setup wizard when this workspace has no company knowledge.
 * Re-runs when `companyId` changes so each workspace is independent.
 * Skip / close does not mark the wizard finished.
 */
export function useCompanyOnboardingWizard(userPresent: boolean, companyId: string | undefined) {
  const [showWizard, setShowWizard] = useState(false)

  useEffect(() => {
    if (!userPresent || !companyId) return
    setShowWizard(false)
    sessionStorage.removeItem('wizard_dismissed')
    api
      .companyManualExists()
      .then(status => {
        if (!status.exists && !status.wizardCompleted) setShowWizard(true)
      })
      .catch(() => {})
  }, [userPresent, companyId])

  return { showWizard, setShowWizard }
}
