import type { EvidenceSourceFragment } from '../types'

/** The span type the backend's spanizer gives one whole Markdown table row. */
export const TABLE_ROW_SPAN_TYPE = 'table_row'

/**
 * The PDF search text for a quote built from its source fragments, or null to
 * search the exact quote.
 *
 * A table-row quote is stored as Markdown with pipes, which a PDF's text layer does
 * not have. When evidence is served, the backend adds `search_text` (the row's cell
 * text, from the one shared table-row splitter) to each table-row fragment. Only the
 * stored span type decides; a prose fragment is never altered, pipes or not.
 *
 * Returns null when no fragment is a table row, or when any table-row fragment has
 * no `search_text` (a payload served before the field existed, or a row with no
 * text): the exact quote is then searched, as before, never a partial string.
 */
export function evidenceViewerSearchText(
  fragments: readonly Pick<EvidenceSourceFragment, 'text' | 'span_type' | 'search_text'>[] | null | undefined,
): string | null {
  if (!fragments || !fragments.some((fragment) => fragment.span_type === TABLE_ROW_SPAN_TYPE)) {
    return null
  }
  const parts: string[] = []
  for (const fragment of fragments) {
    if (fragment.span_type === TABLE_ROW_SPAN_TYPE) {
      const searchText = typeof fragment.search_text === 'string' ? fragment.search_text.trim() : ''
      if (!searchText) {
        return null
      }
      parts.push(searchText)
    } else {
      parts.push(typeof fragment.text === 'string' ? fragment.text.trim() : '')
    }
  }
  return parts.join('\n\n')
}
