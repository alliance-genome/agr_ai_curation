import { describe, expect, it } from 'vitest'

import { normalizePdfViewerDocumentUrl } from './viewerDocumentUrl'

describe('normalizePdfViewerDocumentUrl', () => {
  const origin = 'https://curation.example.org'

  it('preserves relative same-origin document paths', () => {
    expect(
      normalizePdfViewerDocumentUrl('/api/documents/123/viewer?download=false#page=2', origin),
    ).toBe('/api/documents/123/viewer?download=false#page=2')
  })

  it('normalizes same-origin absolute urls to relative paths', () => {
    expect(
      normalizePdfViewerDocumentUrl(
        'https://curation.example.org/uploads/document.pdf?token=abc',
        origin,
      ),
    ).toBe('/uploads/document.pdf?token=abc')
  })

  it('rejects cross-origin document urls', () => {
    expect(() => normalizePdfViewerDocumentUrl('https://evil.example.org/file.pdf', origin))
      .toThrow('The viewer only opens files from this site.')
  })

  it('rejects unsupported protocols', () => {
    expect(() => normalizePdfViewerDocumentUrl('javascript:alert(1)', origin))
      .toThrow('This file\'s address is not supported.')
  })

  // Wording approved by Chris on 23 Nov 2026 (the address can be a text file's).
  it('says a missing address cannot be opened', () => {
    expect(() => normalizePdfViewerDocumentUrl('   ', origin)).toThrow('This file has no address to open.')
  })

  it('says an address that cannot be read is not valid', () => {
    expect(() => normalizePdfViewerDocumentUrl('/x', 'not an origin')).toThrow('This file\'s address is not valid.')
  })
})
