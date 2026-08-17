import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { CLOUD_AI_DATA_NOTICE } from './privacyNotices'

const here = dirname(fileURLToPath(import.meta.url))
const srcRoot = join(here, '..')

describe('CLOUD_AI_DATA_NOTICE', () => {
  it('describes cloud transmission and the local-endpoint alternative', () => {
    expect(CLOUD_AI_DATA_NOTICE).toMatch(/cloud OCR \/ AI/i)
    expect(CLOUD_AI_DATA_NOTICE).toMatch(/document images/i)
    expect(CLOUD_AI_DATA_NOTICE).toMatch(/company profile/i)
    expect(CLOUD_AI_DATA_NOTICE).toMatch(/local endpoint/i)
  })

  it('is shown on settings, onboarding, welcome, processing, and remaining upload paths', () => {
    const files = [
      'components/settings/ApiSettingsPanel.tsx',
      'components/OnboardingWizard.tsx',
      'components/WorkspaceWelcome.tsx',
      'components/LeftAgentSidebar.tsx',
      'components/CloudAiNotice.tsx',
      'features/erpShell/ProcessingView.tsx',
      'features/nodeWorkspace/shell/RunComposer.tsx',
      'features/nodeWorkspace/nodes/workflowNodeTypes.tsx',
    ]
    for (const rel of files) {
      const source = readFileSync(join(srcRoot, rel), 'utf8')
      const wired =
        source.includes('CLOUD_AI_DATA_NOTICE') || source.includes('CloudAiNotice')
      expect(wired, rel).toBe(true)
    }
  })
})
