import { describe, expect, it } from 'vitest'
import {
  providerOptionsFromCatalog,
  resolveProviderSelection,
} from './processingWorkflowHeader'

describe('providerOptionsFromCatalog', () => {
  it('reads provider.options from the first catalog entry that has them', () => {
    expect(
      providerOptionsFromCatalog([
        { params: {} },
        { params: { provider: { options: ['Qwen', 'Enhance'] } } },
      ]),
    ).toEqual(['Qwen', 'Enhance'])
  })

  it('falls back to Qwen when catalog has no options', () => {
    expect(providerOptionsFromCatalog([])).toEqual(['Qwen'])
  })
})

describe('resolveProviderSelection', () => {
  it('keeps a configured provider', () => {
    expect(resolveProviderSelection('Enhance', ['Qwen', 'Enhance'])).toBe('Enhance')
  })

  it('maps legacy DeepSeek to the first API option', () => {
    expect(resolveProviderSelection('DeepSeek', ['Qwen'])).toBe('Qwen')
  })
})
