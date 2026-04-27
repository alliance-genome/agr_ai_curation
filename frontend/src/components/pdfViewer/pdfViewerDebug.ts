import { getEnvFlag } from '@/utils/env'
import type {
  PdfEvidenceSpikeInput,
  PdfEvidenceSpikeResult,
  PdfViewerNavigationResult,
} from './pdfEvidenceNavigation'

export const PDF_EVIDENCE_DEBUG_STORAGE_KEY = 'pdf-evidence-debug'
const PDF_EVIDENCE_DEBUG_URL_PARAM = 'pdfEvidenceDebug'
const PDF_EVIDENCE_DEBUG_MAX_ENTRIES = 800

export interface PdfEvidenceDebugEntry {
  timestamp: string
  message: string
  detail?: unknown
}

const pdfEvidenceDebugEntries: PdfEvidenceDebugEntry[] = []
let lastPdfEvidenceNavigationResult: PdfViewerNavigationResult | null = null

declare global {
  interface Window {
    __pdfViewerEvidenceSpike?: (input: PdfEvidenceSpikeInput) => Promise<PdfEvidenceSpikeResult>
    __pdfViewerEvidenceSpikeLastResult?: PdfEvidenceSpikeResult | null
    __pdfViewerEvidenceDebug?: {
      enabled: boolean
      storageKey: string
      setEnabled: (enabled: boolean) => boolean
      getEntries: () => PdfEvidenceDebugEntry[]
      clearEntries: () => void
      getLastResult: () => PdfViewerNavigationResult | null
    }
  }
}

export const truncateDebugText = (value: string, maxLength: number = 240): string => {
  const normalized = value.replace(/\s+/g, ' ').trim()
  if (normalized.length <= maxLength) {
    return normalized
  }

  return `${normalized.slice(0, maxLength)}...`
}

const sanitizePdfEvidenceDebugDetail = (
  value: unknown,
  depth: number = 0,
  seen: WeakSet<object> = new WeakSet(),
): unknown => {
  if (value === null || value === undefined) {
    return value
  }

  if (typeof value === 'string') {
    return value.length > 1200 ? `${value.slice(0, 1200)}...` : value
  }

  if (typeof value === 'number' || typeof value === 'boolean') {
    return value
  }

  if (depth >= 5) {
    return '[max-depth]'
  }

  if (Array.isArray(value)) {
    const items = value.slice(0, 25).map((entry) => sanitizePdfEvidenceDebugDetail(entry, depth + 1, seen))
    return value.length > 25 ? [...items, `[+${value.length - 25} more]`] : items
  }

  if (typeof value === 'object') {
    if (value instanceof DOMRect) {
      return {
        left: value.left,
        top: value.top,
        width: value.width,
        height: value.height,
      }
    }

    if (value instanceof HTMLElement) {
      return {
        type: 'HTMLElement',
        tagName: value.tagName,
        className: value.className,
        text: truncateDebugText(value.textContent ?? '', 120),
      }
    }

    if (seen.has(value as object)) {
      return '[circular]'
    }
    seen.add(value as object)

    const entries = Object.entries(value as Record<string, unknown>).slice(0, 40)
    return Object.fromEntries(
      entries.map(([key, entryValue]) => [
        key,
        sanitizePdfEvidenceDebugDetail(entryValue, depth + 1, seen),
      ]),
    )
  }

  return String(value)
}

const appendPdfEvidenceDebugEntry = (message: string, detail?: unknown) => {
  pdfEvidenceDebugEntries.push({
    timestamp: new Date().toISOString(),
    message,
    detail: detail === undefined ? undefined : sanitizePdfEvidenceDebugDetail(detail),
  })

  if (pdfEvidenceDebugEntries.length > PDF_EVIDENCE_DEBUG_MAX_ENTRIES) {
    pdfEvidenceDebugEntries.splice(0, pdfEvidenceDebugEntries.length - PDF_EVIDENCE_DEBUG_MAX_ENTRIES)
  }
}

const parseDebugFlag = (value: string | null | undefined): boolean | null => {
  if (value === null || value === undefined) {
    return null
  }

  switch (String(value).toLowerCase()) {
    case 'true':
    case '1':
    case 'yes':
    case 'on':
      return true
    case 'false':
    case '0':
    case 'no':
    case 'off':
      return false
    default:
      return null
  }
}

export const isPdfEvidenceDebugEnabled = (): boolean => {
  if (typeof window !== 'undefined') {
    try {
      const url = new URL(window.location.href)
      const fromUrl = parseDebugFlag(url.searchParams.get(PDF_EVIDENCE_DEBUG_URL_PARAM))
      if (fromUrl !== null) {
        return fromUrl
      }
    } catch {
      // Ignore URL parsing issues and continue with other flag sources.
    }

    try {
      const fromLocalStorage = parseDebugFlag(window.localStorage.getItem(PDF_EVIDENCE_DEBUG_STORAGE_KEY))
      if (fromLocalStorage !== null) {
        return fromLocalStorage
      }
    } catch {
      // Ignore storage access issues and continue with env flags.
    }
  }

  return getEnvFlag(['VITE_DEV_MODE', 'DEV_MODE', 'VITE_DEBUG', 'DEBUG'], false)
}

const setPdfEvidenceDebugEnabled = (enabled: boolean): boolean => {
  if (typeof window === 'undefined') {
    return enabled
  }

  try {
    window.localStorage.setItem(PDF_EVIDENCE_DEBUG_STORAGE_KEY, enabled ? '1' : '0')
  } catch {
    // Ignore storage failures; the console helper still returns the requested state.
  }

  return enabled
}

export const logPdfEvidenceDebug = (message: string, detail?: unknown) => {
  if (!isPdfEvidenceDebugEnabled()) {
    return
  }

  appendPdfEvidenceDebugEntry(message, detail)

  if (detail === undefined) {
    console.info(`[PDF EVIDENCE DEBUG] ${message}`)
    return
  }

  console.info(`[PDF EVIDENCE DEBUG] ${message}`, detail)
}

export const setLastPdfEvidenceNavigationResult = (result: PdfViewerNavigationResult | null) => {
  lastPdfEvidenceNavigationResult = result
}

export const getLastPdfEvidenceNavigationResult = (): PdfViewerNavigationResult | null => {
  return lastPdfEvidenceNavigationResult
}

const getPdfEvidenceDebugEntries = (): PdfEvidenceDebugEntry[] => [...pdfEvidenceDebugEntries]

const clearPdfEvidenceDebugEntries = () => {
  pdfEvidenceDebugEntries.splice(0, pdfEvidenceDebugEntries.length)
}

export const installPdfEvidenceDebugWindow = (): (() => void) => {
  const setEnabled = (enabled: boolean) => {
    const nextEnabled = setPdfEvidenceDebugEnabled(enabled)
    window.__pdfViewerEvidenceDebug = {
      enabled: nextEnabled,
      storageKey: PDF_EVIDENCE_DEBUG_STORAGE_KEY,
      setEnabled,
      getEntries: getPdfEvidenceDebugEntries,
      clearEntries: clearPdfEvidenceDebugEntries,
      getLastResult: getLastPdfEvidenceNavigationResult,
    }
    console.info('[PDF EVIDENCE DEBUG] Browser evidence tracing', nextEnabled ? 'enabled' : 'disabled')
    return nextEnabled
  }

  window.__pdfViewerEvidenceDebug = {
    enabled: isPdfEvidenceDebugEnabled(),
    storageKey: PDF_EVIDENCE_DEBUG_STORAGE_KEY,
    setEnabled,
    getEntries: getPdfEvidenceDebugEntries,
    clearEntries: clearPdfEvidenceDebugEntries,
    getLastResult: getLastPdfEvidenceNavigationResult,
  }

  return () => {
    delete window.__pdfViewerEvidenceDebug
  }
}
