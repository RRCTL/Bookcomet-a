import { describe, expect, it } from 'vitest'
import { escapeYamlDoubleQuoted } from './helpers'

describe('escapeYamlDoubleQuoted', () => {
  it('escapes backslashes before quotes', () => {
    expect(escapeYamlDoubleQuoted('say \\"hi')).toBe('say \\\\\\"hi')
  })

  it('escapes a plain double quote', () => {
    expect(escapeYamlDoubleQuoted('say "hi"')).toBe('say \\"hi\\"')
  })
})
