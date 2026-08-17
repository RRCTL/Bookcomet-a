import { renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../services/api'
import { useCompanyOnboardingWizard } from './useCompanyOnboardingWizard'

vi.mock('../services/api', () => ({
  api: {
    companyManualExists: vi.fn(),
  },
}))

const companyManualExists = vi.mocked(api.companyManualExists)

describe('useCompanyOnboardingWizard', () => {
  beforeEach(() => {
    companyManualExists.mockReset()
    sessionStorage.clear()
  })

  it('auto-opens when the workspace has no company knowledge', async () => {
    companyManualExists.mockResolvedValue({ exists: false, wizardCompleted: false })
    const { result } = renderHook(() => useCompanyOnboardingWizard(true, 'ws-new'))
    await waitFor(() => expect(result.current.showWizard).toBe(true))
  })

  it('does not auto-open when knowledge already exists', async () => {
    companyManualExists.mockResolvedValue({ exists: true, wizardCompleted: false })
    const { result } = renderHook(() => useCompanyOnboardingWizard(true, 'ws-has-knowledge'))
    await waitFor(() => expect(companyManualExists).toHaveBeenCalled())
    expect(result.current.showWizard).toBe(false)
  })

  it('does not auto-open after that workspace wizard has finished', async () => {
    companyManualExists.mockResolvedValue({ exists: true, wizardCompleted: true })
    const { result } = renderHook(() => useCompanyOnboardingWizard(true, 'ws-done'))
    await waitFor(() => expect(companyManualExists).toHaveBeenCalled())
    expect(result.current.showWizard).toBe(false)
  })

  it('re-checks independently when the workspace changes', async () => {
    companyManualExists
      .mockResolvedValueOnce({ exists: true, wizardCompleted: true })
      .mockResolvedValueOnce({ exists: false, wizardCompleted: false })

    const { result, rerender } = renderHook(
      ({ companyId }) => useCompanyOnboardingWizard(true, companyId),
      { initialProps: { companyId: 'ws-a' } },
    )
    await waitFor(() => expect(companyManualExists).toHaveBeenCalledTimes(1))
    expect(result.current.showWizard).toBe(false)

    rerender({ companyId: 'ws-b' })
    await waitFor(() => expect(result.current.showWizard).toBe(true))
    expect(companyManualExists).toHaveBeenCalledTimes(2)
  })
})
