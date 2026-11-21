import { describe, expect, it } from 'vitest'

import type { DomainEnvelopeEvidenceAnchorProjection, EvidenceRecord } from '../types'
import {
  buildNavigationCommandFromChatEvidenceRecord,
  buildNavigationCommandFromEnvelopeEvidenceProjection,
} from './navigationSourceAdapters'
import { evidenceViewerSearchText } from './evidenceViewerSearchText'

// Made-up rows in the exact stored form of a whole-row table piece (P3/P4). The
// search_text values are what the backend serves next to each table-row fragment.
const SHORT_ROW = '| XS1 | 3 | 5 | 4.0 |'
const SHORT_ROW_SEARCH = 'XS1 3 5 4.0'
const PROSE_WITH_PIPE = 'Flies were w[1118] | y[1] at 25 C.'

describe('evidenceViewerSearchText', () => {
  it('uses the search text the backend serves for a table-row fragment', () => {
    expect(evidenceViewerSearchText([
      { text: SHORT_ROW, span_type: 'table_row', search_text: SHORT_ROW_SEARCH },
    ])).toBe(SHORT_ROW_SEARCH)
  })

  it('never alters prose, even prose containing a pipe, alone or next to a row', () => {
    expect(evidenceViewerSearchText([{ text: PROSE_WITH_PIPE, span_type: 'sentence' }])).toBeNull()
    expect(evidenceViewerSearchText([
      { text: PROSE_WITH_PIPE, span_type: 'sentence' },
      { text: SHORT_ROW, span_type: 'table_row', search_text: SHORT_ROW_SEARCH },
    ])).toBe(`${PROSE_WITH_PIPE}\n\n${SHORT_ROW_SEARCH}`)
  })

  it('searches the exact quote when a table-row fragment has no served search text', () => {
    // A payload served or cached before the field existed, or a row with no text:
    // no splitter of its own, and never a partial search text.
    expect(evidenceViewerSearchText([{ text: SHORT_ROW, span_type: 'table_row' }])).toBeNull()
    expect(evidenceViewerSearchText([
      { text: 'Strains are listed below.', span_type: 'sentence' },
      { text: '', span_type: 'table_row' },
    ])).toBeNull()
    expect(evidenceViewerSearchText(undefined)).toBeNull()
  })
})

function chatRecord(overrides: Partial<EvidenceRecord> = {}): EvidenceRecord {
  return {
    entity: 'zeta',
    verified_quote: SHORT_ROW,
    page: 4,
    section: 'Results',
    chunk_id: 'chunk-4',
    source_fragments: [{ text: SHORT_ROW, span_type: 'table_row', search_text: SHORT_ROW_SEARCH }],
    ...overrides,
  }
}

describe('evidence navigation for table-row quotes', () => {
  it('searches by the served cell text and keeps the exact quote everywhere else', () => {
    const record = chatRecord()
    const command = buildNavigationCommandFromChatEvidenceRecord(record)

    expect(command.searchText).toBe(SHORT_ROW_SEARCH)
    expect(command.anchor.viewer_search_text).toBe(SHORT_ROW_SEARCH)
    // Display, copying and the viewer's navigation result read these: the exact quote.
    expect(command.anchor.snippet_text).toBe(SHORT_ROW)
    expect(command.anchor.sentence_text).toBe(SHORT_ROW)
    expect(command.anchor.normalized_text).toBe(SHORT_ROW)
    expect(record.verified_quote).toBe(SHORT_ROW)
  })

  it('leaves a prose quote with a pipe unchanged', () => {
    const command = buildNavigationCommandFromChatEvidenceRecord(chatRecord({
      verified_quote: PROSE_WITH_PIPE,
      source_fragments: [{ text: PROSE_WITH_PIPE, span_type: 'sentence' }],
    }))
    expect(command.searchText).toBe(PROSE_WITH_PIPE)
  })

  it('searches the exact quote for a table row served without the field', () => {
    const command = buildNavigationCommandFromChatEvidenceRecord(chatRecord({
      source_fragments: [{ text: SHORT_ROW, span_type: 'table_row' }],
    }))
    expect(command.searchText).toBe(SHORT_ROW)
  })

  it('searches a review-grid table-row quote by the cell text served on its source record', () => {
    const projection = {
      anchor_id: 'projection-1',
      evidence_record_id: 'evidence-1',
      envelope_id: 'envelope-1',
      object_id: 'object-1',
      envelope_revision: 1,
      quote: SHORT_ROW,
      page_number: 2,
      chunk_ids: ['chunk-2'],
      anchor: {
        anchor_kind: 'snippet',
        locator_quality: 'exact_quote',
        supports_decision: 'supports',
        snippet_text: SHORT_ROW,
        sentence_text: SHORT_ROW,
        viewer_search_text: SHORT_ROW,
        page_number: 2,
        chunk_ids: ['chunk-2'],
      },
      metadata: {
        source_record: {
          verified_quote: SHORT_ROW,
          source_fragments: [{ text: SHORT_ROW, span_type: 'table_row', search_text: SHORT_ROW_SEARCH }],
        },
      },
    } as DomainEnvelopeEvidenceAnchorProjection

    const command = buildNavigationCommandFromEnvelopeEvidenceProjection(projection)
    expect(command?.searchText).toBe(SHORT_ROW_SEARCH)
    expect(command?.anchor.snippet_text).toBe(SHORT_ROW)
  })
})
