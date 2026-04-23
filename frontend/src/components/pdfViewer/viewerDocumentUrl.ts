const SUPPORTED_VIEWER_PROTOCOLS = new Set(['http:', 'https:'])

const toRelativeViewerPath = (url: URL): string => {
  return `${url.pathname}${url.search}${url.hash}`
}

export function normalizePdfViewerDocumentUrl(
  input: string,
  origin: string = window.location.origin,
): string {
  if (typeof input !== 'string' || input.trim().length === 0) {
    throw new Error('A PDF document URL is required.')
  }

  let baseOrigin: URL
  let resolvedUrl: URL

  try {
    baseOrigin = new URL(origin)
    resolvedUrl = new URL(input, baseOrigin)
  } catch (_error) {
    throw new Error('The PDF document URL is invalid.')
  }

  if (!SUPPORTED_VIEWER_PROTOCOLS.has(resolvedUrl.protocol)) {
    throw new Error('Unsupported PDF document URL protocol.')
  }

  if (resolvedUrl.origin !== baseOrigin.origin) {
    throw new Error('The PDF viewer only supports same-origin document URLs.')
  }

  return toRelativeViewerPath(resolvedUrl)
}
