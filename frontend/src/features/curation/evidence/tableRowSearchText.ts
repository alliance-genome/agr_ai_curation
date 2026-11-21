import type { EvidenceSourceFragment } from '../types'

/** The span type the backend's spanizer gives one whole Markdown table row. */
export const TABLE_ROW_SPAN_TYPE = 'table_row'

const UNESCAPED_PIPE = /(?<!\\)\|/

/**
 * The text a PDF shows for one stored table row: its cells, trimmed, with
 * escaped pipes restored, joined by single spaces.
 *
 * A table-row quote is stored as Markdown (`| XS0401 | zeta401. | 25 C |`), while a
 * PDF's text layer has no pipes, so a short row would score below the match
 * threshold. This reads only the row's Markdown structure (unescaped pipes), the
 * same rule `backend/src/lib/pipeline/table_chunking.py` `split_table_cells` uses.
 */
export function tableRowSearchText(row: string): string {
  let stripped = row.trim()
  if (stripped.startsWith('|')) {
    stripped = stripped.slice(1)
  }
  if (stripped.endsWith('|') && !stripped.endsWith('\\|')) {
    stripped = stripped.slice(0, -1)
  }
  return stripped
    .split(UNESCAPED_PIPE)
    .map((cell) => cell.replace(/\\\|/g, '|').trim())
    .filter((cell) => cell.length > 0)
    .join(' ')
}

/**
 * The PDF search text for a quote built from its source fragments, or null when
 * none of them is a table row (the quote itself is then searched, as before).
 * Fragments are joined the way the verified quote joins them.
 */
export function evidenceViewerSearchText(
  fragments: readonly Pick<EvidenceSourceFragment, 'text' | 'span_type'>[] | null | undefined,
): string | null {
  if (!fragments || !fragments.some((fragment) => fragment.span_type === TABLE_ROW_SPAN_TYPE)) {
    return null
  }
  const parts = fragments.map((fragment) => {
    const text = typeof fragment.text === 'string' ? fragment.text : ''
    return fragment.span_type === TABLE_ROW_SPAN_TYPE ? tableRowSearchText(text) : text.trim()
  })
  return parts.join('\n\n')
}
