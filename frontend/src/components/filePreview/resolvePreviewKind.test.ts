import { describe, expect, it } from 'vitest'
import { guessMimeFromFilename, resolvePreviewKind } from './resolvePreviewKind'

describe('resolvePreviewKind', () => {
  it('detects images from mime type', () => {
    expect(resolvePreviewKind('image/png', 'doc.bin')).toBe('image')
  })

  it('detects pdf from mime type', () => {
    expect(resolvePreviewKind('application/pdf', 'doc.bin')).toBe('pdf')
  })

  it('detects png from extension', () => {
    expect(resolvePreviewKind('', 'receipt.png')).toBe('image')
  })

  it('detects jpg from extension', () => {
    expect(resolvePreviewKind('', 'photo.JPG')).toBe('image')
  })

  it('detects pdf from extension', () => {
    expect(resolvePreviewKind('', 'statement.pdf')).toBe('pdf')
  })

  it('marks csv as unsupported', () => {
    expect(resolvePreviewKind('text/csv', 'data.csv')).toBe('unsupported')
  })
})

describe('guessMimeFromFilename', () => {
  it('guesses pdf mime', () => {
    expect(guessMimeFromFilename('a.pdf')).toBe('application/pdf')
  })
})
