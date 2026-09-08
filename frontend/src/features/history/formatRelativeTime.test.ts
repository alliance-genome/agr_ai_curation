import { describe, expect, it } from 'vitest'

import formatRelativeTime, { formatExactTime } from './formatRelativeTime'

const NOW = new Date('2026-09-02T14:00:00Z')
const OPTIONS = { now: NOW, locale: 'en-US', timeZone: 'UTC' }

describe('formatRelativeTime', () => {
  it('returns Just now inside the first minute', () => {
    expect(formatRelativeTime('2026-09-02T13:59:40Z', OPTIONS)).toBe('Just now')
  })

  it('formats minutes within the hour', () => {
    expect(formatRelativeTime('2026-09-02T13:48:00Z', OPTIONS)).toBe('12 min ago')
    expect(formatRelativeTime('2026-09-02T13:59:00Z', OPTIONS)).toBe('1 min ago')
  })

  it('formats hours within the day', () => {
    expect(formatRelativeTime('2026-09-02T12:00:00Z', OPTIONS)).toBe('2 h ago')
    expect(formatRelativeTime('2026-09-01T15:00:00Z', OPTIONS)).toBe('23 h ago')
  })

  it('formats yesterday with the clock time once a full day passed', () => {
    expect(formatRelativeTime('2026-09-01T09:42:00Z', OPTIONS)).toBe('Yesterday 9:42 AM')
  })

  it('formats older dates in the same year as month and day', () => {
    expect(formatRelativeTime('2026-08-28T09:42:00Z', OPTIONS)).toBe('Aug 28')
  })

  it('formats dates from another year with the year', () => {
    expect(formatRelativeTime('2025-12-30T09:42:00Z', OPTIONS)).toBe('Dec 30, 2025')
  })

  it('treats a future timestamp as Just now', () => {
    expect(formatRelativeTime('2026-09-02T14:05:00Z', OPTIONS)).toBe('Just now')
  })

  it('reports missing or invalid values as Unavailable', () => {
    expect(formatRelativeTime(null, OPTIONS)).toBe('Unavailable')
    expect(formatRelativeTime(undefined, OPTIONS)).toBe('Unavailable')
    expect(formatRelativeTime('', OPTIONS)).toBe('Unavailable')
    expect(formatRelativeTime('not a date', OPTIONS)).toBe('Unavailable')
  })
})

describe('formatExactTime', () => {
  it('uses the full locale string for valid values', () => {
    expect(formatExactTime('2026-08-28T09:42:00Z')).toBe(new Date('2026-08-28T09:42:00Z').toLocaleString())
  })

  it('reports missing or invalid values as Unavailable', () => {
    expect(formatExactTime(null)).toBe('Unavailable')
    expect(formatExactTime('garbage')).toBe('Unavailable')
  })
})
