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
      .toThrow('same-origin')
  })

  it('rejects unsupported protocols', () => {
    expect(() => normalizePdfViewerDocumentUrl('javascript:alert(1)', origin))
      .toThrow('Unsupported PDF document URL protocol.')
  })
})
