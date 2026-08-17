import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { STEPS } from './OnboardingWizard'

const CJK = /[\u3400-\u9fff]/

describe('OnboardingWizard English-only chrome', () => {
  it('uses English step titles and subtitles only', () => {
    for (const step of STEPS) {
      expect(step.title, step.id).not.toMatch(CJK)
      expect(step.subtitle, step.id).not.toMatch(CJK)
      expect(step.description, step.id).not.toMatch(CJK)
      if (step.hint) expect(step.hint, step.id).not.toMatch(CJK)
    }
  })

  it('keeps wizard source chrome free of Chinese labels and placeholders', () => {
    const source = readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), 'OnboardingWizard.tsx'),
      'utf8',
    )
    expect(source).not.toMatch(CJK)
  })
})
