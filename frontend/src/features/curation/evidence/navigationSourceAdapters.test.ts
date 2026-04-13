import { describe, expect, it } from 'vitest'

import type { EvidenceRecord, CurationEvidenceRecord } from '../types'
import {
  buildNavigationCommandFromChatEvidenceRecord,
  buildNavigationCommandFromCurationEvidenceRecord,
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

function makeCurationEvidenceRecord(
  overrides: Partial<CurationEvidenceRecord> = {},
): CurationEvidenceRecord {
  const { anchor: anchorOverrides, ...recordOverrides } = overrides as Partial<
    CurationEvidenceRecord
  > & {
    anchor?: Partial<CurationEvidenceRecord['anchor']>
  }

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
