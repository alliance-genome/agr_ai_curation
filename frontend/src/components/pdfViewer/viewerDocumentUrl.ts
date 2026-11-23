const SUPPORTED_VIEWER_PROTOCOLS = new Set(['http:', 'https:'])

const toRelativeViewerPath = (url: URL): string => {
  return `${url.pathname}${url.search}${url.hash}`
}

export function normalizePdfViewerDocumentUrl(
  input: string,
  origin: string = window.location.origin,
): string {
  if (typeof input !== 'string' || input.trim().length === 0) {
    throw new Error('This file has no address to open.')
  }

  let baseOrigin: URL
  let resolvedUrl: URL

  try {
    baseOrigin = new URL(origin)
    resolvedUrl = new URL(input, baseOrigin)
  } catch (_error) {
    throw new Error('This file\'s address is not valid.')
  }

  if (!SUPPORTED_VIEWER_PROTOCOLS.has(resolvedUrl.protocol)) {
    throw new Error('This file\'s address is not supported.')
  }

  if (resolvedUrl.origin !== baseOrigin.origin) {
    throw new Error('The viewer only opens files from this site.')
  }

  return toRelativeViewerPath(resolvedUrl)
}
