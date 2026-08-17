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

  it('is shown on settings, onboarding, welcome, and processing upload', () => {
    const files = [
      'components/settings/ApiSettingsPanel.tsx',
      'components/OnboardingWizard.tsx',
      'components/WorkspaceWelcome.tsx',
      'features/erpShell/ProcessingView.tsx',
    ]
    for (const rel of files) {
      const source = readFileSync(join(srcRoot, rel), 'utf8')
      expect(source, rel).toContain('CLOUD_AI_DATA_NOTICE')
    }
  })
})
