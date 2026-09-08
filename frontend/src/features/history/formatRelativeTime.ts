export interface RelativeTimeOptions {
  now?: Date
  locale?: string
  timeZone?: string
}

export const UNAVAILABLE_TIME_LABEL = 'Unavailable'

const MINUTE_MS = 60_000
const HOUR_MS = 60 * MINUTE_MS
const DAY_MS = 24 * HOUR_MS

function parseDate(value?: string | null): Date | null {
  if (!value) {
    return null
  }

  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

function calendarDayKey(date: Date, locale?: string, timeZone?: string): string {
  return date.toLocaleDateString(locale, { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' })
}

function isCalendarYesterday(date: Date, now: Date, locale?: string, timeZone?: string): boolean {
  const yesterday = new Date(now.getTime() - DAY_MS)
  return calendarDayKey(date, locale, timeZone) === calendarDayKey(yesterday, locale, timeZone)
}

function isSameCalendarYear(date: Date, now: Date, locale?: string, timeZone?: string): boolean {
  const options: Intl.DateTimeFormatOptions = { timeZone, year: 'numeric' }
  return date.toLocaleDateString(locale, options) === now.toLocaleDateString(locale, options)
}

export default function formatRelativeTime(
  value?: string | null,
  { now = new Date(), locale, timeZone }: RelativeTimeOptions = {},
): string {
  const date = parseDate(value)
  if (!date) {
    return UNAVAILABLE_TIME_LABEL
  }

  const elapsedMs = now.getTime() - date.getTime()

  if (elapsedMs < MINUTE_MS) {
    return 'Just now'
  }

  if (elapsedMs < HOUR_MS) {
    return `${Math.floor(elapsedMs / MINUTE_MS)} min ago`
  }

  if (elapsedMs < DAY_MS) {
    return `${Math.floor(elapsedMs / HOUR_MS)} h ago`
  }

  if (isCalendarYesterday(date, now, locale, timeZone)) {
    const clockTime = date.toLocaleTimeString(locale, { timeZone, hour: 'numeric', minute: '2-digit' })
    return `Yesterday ${clockTime}`
  }

  if (isSameCalendarYear(date, now, locale, timeZone)) {
    return date.toLocaleDateString(locale, { timeZone, month: 'short', day: 'numeric' })
  }

  return date.toLocaleDateString(locale, { timeZone, month: 'short', day: 'numeric', year: 'numeric' })
}

export function formatExactTime(value?: string | null): string {
  const date = parseDate(value)
  return date ? date.toLocaleString() : UNAVAILABLE_TIME_LABEL
}
