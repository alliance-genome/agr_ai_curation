import { describe, expect, it } from 'vitest'

import type {
  CurationEvidenceRecord,
  DomainEnvelopeEvidenceAnchorProjection,
  EvidenceRecord,
} from '../types'
import {
  buildNavigationCommandFromChatEvidenceRecord,
  buildNavigationCommandFromCurationEvidenceRecord,
  buildNavigationCommandFromEnvelopeEvidenceProjection,
} from './navigationSourceAdapters'

function makeChatEvidenceRecord(
  overrides: Partial<EvidenceRecord> = {},
): EvidenceRecord {
  return {
    entity: 'crb',
    verified_quote:
      'these, crb 11A22 (null allele) and crb 8F105 (point mutation encoding a truncated protein lacking 23 amino acids), display abnormal PRC morphology in adult eyes, with bulky and closely apposed rhabdomeres',
    page: 7,
    section: 'Results',
    subsection: 'Photoreceptor Morphology',
    chunk_id: 'chunk-crb-7',
    figure_reference: 'Figure 2',
    ...overrides,
  }
}

function makeEnvelopeEvidenceProjection(): DomainEnvelopeEvidenceAnchorProjection {
  return {
    anchor_id: 'projection-anchor-1',
    evidence_record_id: 'evidence-record-1',
    envelope_id: 'envelope-1',
    object_id: 'object-1',
    object_type: 'Gene',
    field_path: 'gene.symbol',
    envelope_revision: 3,
    document_id: 'document-1',
    quote: 'The projected gene symbol is crb.',
    page_number: 4,
    page_label: '4',
    chunk_id: 'chunk-4',
    chunk_ids: ['chunk-4'],
    section_title: 'Results',
    subsection_title: 'Expression',
    figure_reference: null,
    table_reference: null,
    source_id: null,
    source_title: null,
    source_url: null,
    anchor: {
      anchor_kind: 'snippet',
      locator_quality: 'exact_quote',
      supports_decision: 'supports',
      viewer_search_text: 'Persisted exact viewer search text.',
      sentence_text: 'The projected gene symbol is crb.',
      snippet_text: 'The projected gene symbol is crb.',
      page_number: 4,
      section_title: 'Results',
      subsection_title: 'Expression',
      chunk_ids: ['chunk-4'],
    },
    metadata: {},
  }
}

function makeCurationEvidenceRecord(
  overrides: Omit<Partial<CurationEvidenceRecord>, 'anchor'> & {
    anchor?: Partial<CurationEvidenceRecord['anchor']>
  } = {},
): CurationEvidenceRecord {
  const { anchor: anchorOverrides, ...recordOverrides } = overrides

  return {
    anchor_id: 'anchor-crb-7',
    candidate_id: 'candidate-1',
    source: 'extracted',
    field_keys: ['gene_symbol'],
    field_group_keys: ['identity'],
    is_primary: true,
    anchor: {
      anchor_kind: 'snippet',
      locator_quality: 'normalized_quote',
      supports_decision: 'supports',
      sentence_text:
        'these, crb 11A22 (null allele) and crb 8F105 (point mutation encoding a truncated protein lacking 23 amino acids), display abnormal PRC morphology in adult eyes, with bulky and closely apposed rhabdomeres',
      snippet_text:
        'these, crb 11A22 (null allele) and crb 8F105 (point mutation encoding a truncated protein lacking 23 amino acids), display abnormal PRC morphology in adult eyes, with bulky and closely apposed rhabdomeres',
      normalized_text:
        'these, crb 11A22 (null allele) and crb 8F105 (point mutation encoding a truncated protein lacking 23 amino acids), display abnormal PRC morphology in adult eyes, with bulky and closely apposed rhabdomeres',
      viewer_search_text:
        'Results: these, crb 11A22 (null allele) and crb 8F105 (point mutation encoding a truncated protein lacking 23 amino acids), display abnormal PRC morphology in adult eyes, with bulky and closely apposed rhabdomeres',
      viewer_highlightable: true,
      page_number: 7,
      section_title: 'Results',
      subsection_title: 'Photoreceptor Morphology',
      figure_reference: 'Figure 2',
      chunk_ids: ['chunk-crb-7'],
      ...anchorOverrides,
    },
    created_at: '2026-04-13T00:00:00Z',
    updated_at: '2026-04-13T00:00:00Z',
    warnings: [],
    ...recordOverrides,
  }
}

describe('navigationSourceAdapters', () => {
  it('builds an exact viewer command from an envelope evidence projection', () => {
    const command = buildNavigationCommandFromEnvelopeEvidenceProjection(
      makeEnvelopeEvidenceProjection(),
    )!

    expect(command).toMatchObject({
      anchorId: 'projection-anchor-1',
      searchText: 'The projected gene symbol is crb.',
      pageNumber: 4,
      sectionTitle: 'Results',
      mode: 'select',
    })
    expect(command.anchor.subsection_title).toBe('Expression')
  })

  it('normalizes envelope quote and hierarchy text before building viewer commands', () => {
    const projection = makeEnvelopeEvidenceProjection()
    projection.quote = '  The projected gene symbol is crb.  '
    projection.section_title = '   '
    projection.subsection_title = '  Expression  '
    projection.anchor = {
      ...projection.anchor,
      viewer_search_text: '',
      sentence_text: 'A noisier persisted sentence.',
      section_title: ' Results ',
      subsection_title: null,
    }

    const command = buildNavigationCommandFromEnvelopeEvidenceProjection(projection)!

    expect(command.searchText).toBe('The projected gene symbol is crb.')
    expect(command.sectionTitle).toBe('Results')
    expect(command.anchor.section_title).toBe('Results')
    expect(command.anchor.subsection_title).toBe('Expression')
    expect(command.anchor.viewer_search_text).toBe('The projected gene symbol is crb.')
  })

  it('returns null for envelope evidence projections without navigable text or location', () => {
    const projection = makeEnvelopeEvidenceProjection()
    projection.quote = '   '
    projection.page_number = null
    projection.section_title = '   '
    projection.subsection_title = null
    projection.anchor = {
      ...projection.anchor,
      viewer_search_text: '',
      sentence_text: null,
      snippet_text: null,
      normalized_text: null,
      page_number: null,
      section_title: null,
      subsection_title: null,
    }

    expect(buildNavigationCommandFromEnvelopeEvidenceProjection(projection)).toBeNull()
  })

  it('derives the same quote-centric viewer input for equivalent chat and curation evidence', () => {
    const chatCommand = buildNavigationCommandFromChatEvidenceRecord(
      makeChatEvidenceRecord(),
    )
    const curationCommand = buildNavigationCommandFromCurationEvidenceRecord(
      makeCurationEvidenceRecord(),
    )

    expect(curationCommand).not.toBeNull()
    expect(chatCommand.searchText).toBe(
      curationCommand?.searchText,
    )
    expect(chatCommand.pageNumber).toBe(
      curationCommand?.pageNumber,
    )
    expect(chatCommand.sectionTitle).toBe(
      curationCommand?.sectionTitle,
    )
    expect(chatCommand.anchor.sentence_text).toBe(
      curationCommand?.anchor.sentence_text,
    )
    expect(chatCommand.anchor.snippet_text).toBe(
      curationCommand?.anchor.snippet_text,
    )
    expect(chatCommand.anchor.viewer_search_text).toBe(
      curationCommand?.anchor.viewer_search_text,
    )
    expect(chatCommand.anchor.subsection_title).toBe(
      curationCommand?.anchor.subsection_title,
    )
  })

  it('prefers the human-visible quote text over noisier persisted search text', () => {
    const command = buildNavigationCommandFromCurationEvidenceRecord(
      makeCurationEvidenceRecord({
        anchor: {
          sentence_text:
            'crb accumulated to a higher molar abundance in mutant fly eyes.',
          snippet_text:
            'crb accumulated to a higher molar abundance in mutant fly eyes.',
          normalized_text:
            'crb accumulated to a higher molar abundance in mutant fly eyes.',
          viewer_search_text:
            '2.3. crb accumulated to a higher molar abundance in mutant fly eyes.',
        },
      }),
    )

    expect(command?.searchText).toBe(
      'crb accumulated to a higher molar abundance in mutant fly eyes.',
    )
    expect(command?.anchor.sentence_text).toBe(
      'crb accumulated to a higher molar abundance in mutant fly eyes.',
    )
    expect(command?.anchor.snippet_text).toBe(
      'crb accumulated to a higher molar abundance in mutant fly eyes.',
    )
    expect(command?.anchor.viewer_search_text).toBe(
      'crb accumulated to a higher molar abundance in mutant fly eyes.',
    )
  })

  it('splits combined curation section paths into chat-like section and subsection fields', () => {
    const command = buildNavigationCommandFromCurationEvidenceRecord(
      makeCurationEvidenceRecord({
        anchor: {
          sentence_text:
            'Changes in Molecular Organization Following Abnormal PRC Development in crumbs Mutants',
          snippet_text:
            'Changes in Molecular Organization Following Abnormal PRC Development in crumbs Mutants',
          normalized_text:
            'Changes in Molecular Organization Following Abnormal PRC Development in crumbs Mutants',
          viewer_search_text:
            'Changes in Molecular Organization Following Abnormal PRC Development in crumbs Mutants',
          section_title:
            '2. Results and Discussion > 2.6. Changes in Molecular Organization Following Abnormal PRC Development in crumbs Mutants',
          subsection_title: null,
          page_number: 1,
        },
      }),
    )

    expect(command?.sectionTitle).toBe('2. Results and Discussion')
    expect(command?.anchor.section_title).toBe('2. Results and Discussion')
    expect(command?.anchor.subsection_title).toBe(
      '2.6. Changes in Molecular Organization Following Abnormal PRC Development in crumbs Mutants',
    )
  })

  it('normalizes mixed-shape curation hierarchy when the persisted section path already includes the subsection', () => {
    const command = buildNavigationCommandFromCurationEvidenceRecord(
      makeCurationEvidenceRecord({
        anchor: {
          sentence_text:
            'Changes in Molecular Organization Following Abnormal PRC Development in crumbs Mutants',
          snippet_text:
            'Changes in Molecular Organization Following Abnormal PRC Development in crumbs Mutants',
          normalized_text:
            'Changes in Molecular Organization Following Abnormal PRC Development in crumbs Mutants',
          viewer_search_text:
            'Changes in Molecular Organization Following Abnormal PRC Development in crumbs Mutants',
          section_title:
            '2. Results and Discussion > 2.6. Changes in Molecular Organization Following Abnormal PRC Development in crumbs Mutants',
          subsection_title:
            '2.6. Changes in Molecular Organization Following Abnormal PRC Development in crumbs Mutants',
          page_number: 1,
        },
      }),
    )

    expect(command?.sectionTitle).toBe('2. Results and Discussion')
    expect(command?.anchor.section_title).toBe('2. Results and Discussion')
    expect(command?.anchor.subsection_title).toBe(
      '2.6. Changes in Molecular Organization Following Abnormal PRC Development in crumbs Mutants',
    )
  })

  it('preserves degraded section/page/document navigation when a curation anchor has no quote text', () => {
    const command = buildNavigationCommandFromCurationEvidenceRecord(
      makeCurationEvidenceRecord({
        anchor: {
          locator_quality: 'section_only',
          sentence_text: null,
          snippet_text: null,
          normalized_text: null,
          viewer_search_text: null,
          page_number: 7,
          section_title: 'Results',
          subsection_title: 'Photoreceptor Morphology',
        },
      }),
    )

    expect(command).toEqual({
      anchorId: 'anchor-crb-7',
      fileId: null,
      textEvidence: { fragments: null, section: 'Results', subsection: 'Photoreceptor Morphology' },
      anchor: expect.objectContaining({
        locator_quality: 'section_only',
        page_number: 7,
        section_title: 'Results',
        subsection_title: 'Photoreceptor Morphology',
      }),
      searchText: null,
      pageNumber: 7,
      sectionTitle: 'Results',
      mode: 'select',
    })
  })
})

describe('navigation commands name the evidence file (ALL-1460, P9b)', () => {
  const FILE_ID = 'aaaaaaaa-0000-0000-0000-000000000002'

  it('a chat record carries its file id into the command', () => {
    const command = buildNavigationCommandFromChatEvidenceRecord(
      makeChatEvidenceRecord({ file_id: FILE_ID, file_name: 'Table_S1.xlsx' }),
    )

    expect(command.fileId).toBe(FILE_ID)
  })

  it('a chat record saved before files were recorded gives a command with no file', () => {
    const command = buildNavigationCommandFromChatEvidenceRecord(makeChatEvidenceRecord())

    expect(command.fileId).toBeNull()
  })

  it('a page-less chat record builds a command without a page and without "pnull" in its anchor id', () => {
    const command = buildNavigationCommandFromChatEvidenceRecord(
      makeChatEvidenceRecord({ file_id: FILE_ID, file_name: 'Table_S1.xlsx', page: null }),
    )

    expect(command.pageNumber).toBeNull()
    expect(command.anchor.page_number).toBeNull()
    expect(command.anchorId).not.toContain('pnull')
    expect(command.anchorId).toContain(FILE_ID)
  })

  it('two chat records quoting the same text in two files get different anchor ids', () => {
    const first = buildNavigationCommandFromChatEvidenceRecord(
      makeChatEvidenceRecord({ file_id: FILE_ID, file_name: 'a.xlsx', page: null }),
    )
    const second = buildNavigationCommandFromChatEvidenceRecord(
      makeChatEvidenceRecord({ file_id: 'aaaaaaaa-0000-0000-0000-000000000003', file_name: 'b.pdf', page: null }),
    )

    expect(first.anchorId).not.toBe(second.anchorId)
  })

  it('a review-grid record carries the file id that sits beside its anchor', () => {
    const command = buildNavigationCommandFromCurationEvidenceRecord(
      makeCurationEvidenceRecord({ file_id: FILE_ID }),
    )

    expect(command?.fileId).toBe(FILE_ID)
  })

  it('a review-grid record saved before files were recorded gives a command with no file', () => {
    const command = buildNavigationCommandFromCurationEvidenceRecord(makeCurationEvidenceRecord())

    expect(command?.fileId).toBeNull()
  })

  it('an envelope projection carries its file id', () => {
    const command = buildNavigationCommandFromEnvelopeEvidenceProjection({
      ...makeEnvelopeEvidenceProjection(),
      file_id: FILE_ID,
    })

    expect(command?.fileId).toBe(FILE_ID)
  })

  it('an envelope projection saved before files were recorded gives a command with no file', () => {
    const command = buildNavigationCommandFromEnvelopeEvidenceProjection(makeEnvelopeEvidenceProjection())

    expect(command?.fileId).toBeNull()
  })
})

describe('navigation commands carry the stored fragments and raw section names (ALL-1460, P9b)', () => {
  const FILE_ID = 'aaaaaaaa-0000-0000-0000-000000000002'

  // The record_evidence writer's shape: verified_quote is the span texts joined by a
  // blank line; each fragment keeps its own exact text, section and subsection.
  it('a chat record\'s fragments are kept one by one, unchanged', () => {
    const command = buildNavigationCommandFromChatEvidenceRecord(makeChatEvidenceRecord({
      verified_quote: '| Strain | Temp |\n\n| XS0401 | 25 C |',
      page: null,
      section: 'Table S1',
      subsection: 'Sheet 1',
      file_id: FILE_ID,
      file_name: 'Table_S1.xlsx',
      source_fragments: [
        { text: '| Strain | Temp |', span_type: 'table_row', section: 'Table S1', subsection: 'Sheet 1', file_id: FILE_ID },
        { text: '| XS0401 | 25 C |', span_type: 'table_row', section: 'Table S1', subsection: 'Sheet 1', file_id: FILE_ID },
      ],
    }))

    expect(command.textEvidence).toEqual({
      fragments: [
        { text: '| Strain | Temp |', section: 'Table S1', subsection: 'Sheet 1' },
        { text: '| XS0401 | 25 C |', section: 'Table S1', subsection: 'Sheet 1' },
      ],
      section: 'Table S1',
      subsection: 'Sheet 1',
    })
  })

  it('a section name containing " > " reaches the text locator unsplit', () => {
    const command = buildNavigationCommandFromChatEvidenceRecord(makeChatEvidenceRecord({
      section: 'Growth > 25 C',
      subsection: undefined,
      file_id: FILE_ID,
    }))

    expect(command.textEvidence).toMatchObject({ section: 'Growth > 25 C', subsection: null })
  })

  it('an envelope projection takes the fragments of its stored record', () => {
    const projection = makeEnvelopeEvidenceProjection()
    const command = buildNavigationCommandFromEnvelopeEvidenceProjection({
      ...projection,
      file_id: FILE_ID,
      metadata: {
        source_record: {
          source_fragments: [
            { text: 'Worms were grown.', section: 'Methods' },
            { text: 'They were kept at 25 C.', section: 'Methods' },
          ],
        },
      },
    })

    expect(command?.textEvidence.fragments).toEqual([
      { text: 'Worms were grown.', section: 'Methods', subsection: null },
      { text: 'They were kept at 25 C.', section: 'Methods', subsection: null },
    ])
  })

  it('a review-grid anchor has no fragments: the quote is located', () => {
    const command = buildNavigationCommandFromCurationEvidenceRecord(makeCurationEvidenceRecord({ file_id: FILE_ID }))

    expect(command?.textEvidence.fragments).toBeNull()
  })
})
