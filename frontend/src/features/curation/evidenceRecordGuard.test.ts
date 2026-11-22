import { describe, expect, it } from 'vitest'

import { isEvidenceRecord } from './evidenceRecordGuard'

const base = {
  entity: 'XS0401',
  verified_quote: '| XS0401 | 25 C |',
  section: 'Sheet 1',
  chunk_id: 'chunk-36',
}

describe('isEvidenceRecord', () => {
  it('keeps a record saved before files were recorded (no file id, a page)', () => {
    expect(isEvidenceRecord({ ...base, page: 3 })).toBe(true)
  })

  it('keeps a page-less record that names its file (P8b shape)', () => {
    expect(isEvidenceRecord({
      ...base,
      page: null,
      file_id: 'aaaaaaaa-0000-0000-0000-000000000002',
      file_name: 'Table_S1.xlsx',
    })).toBe(true)
  })

  it('keeps a record that names its file and has no section (text before the file\'s first heading)', () => {
    expect(isEvidenceRecord({
      ...base,
      section: null,
      page: null,
      file_id: 'aaaaaaaa-0000-0000-0000-000000000002',
      file_name: 'Table_S1.xlsx',
    })).toBe(true)
  })

  it('mirrors the backend\'s exact rules: a blank file id is no file; without one, a positive whole page and a non-blank section', () => {
    expect(isEvidenceRecord({ ...base, page: null, section: null, file_id: '   ' })).toBe(false)
    expect(isEvidenceRecord({ ...base, page: 0 })).toBe(false)
    expect(isEvidenceRecord({ ...base, page: 2.5 })).toBe(false)
    expect(isEvidenceRecord({ ...base, page: true })).toBe(false)
    expect(isEvidenceRecord({ ...base, page: 3, section: '  ' })).toBe(false)
    expect(isEvidenceRecord({ ...base, page: null, section: '  ', file_id: 'aaaaaaaa-0000-0000-0000-000000000002' })).toBe(true)
  })

  it('drops a section-less record without a file', () => {
    expect(isEvidenceRecord({ ...base, page: 3, section: null })).toBe(false)
  })

  it('drops a page-less record without a file, and records missing required fields', () => {
    expect(isEvidenceRecord({ ...base, page: null })).toBe(false)
    expect(isEvidenceRecord({ ...base })).toBe(false)
    expect(isEvidenceRecord({ ...base, page: 3, chunk_id: undefined })).toBe(false)
    expect(isEvidenceRecord(null)).toBe(false)
  })
})
