import { describe, expect, it } from 'vitest'

import { locateQuoteInBlocks, locateSectionInBlocks } from './viewerFileNavigation'
import type { DisplayBlock } from './viewerFiles'
import { TEXT_SUPPLEMENT_BLOCKS } from './viewerFilesFixtures'

const blocks = TEXT_SUPPLEMENT_BLOCKS.blocks

function paragraph(
  text: string,
  sections: Partial<Pick<DisplayBlock, 'section_title' | 'parent_section' | 'subsection'>> = {},
): DisplayBlock {
  return {
    kind: 'paragraph',
    text,
    level: null,
    section_title: sections.section_title ?? null,
    parent_section: sections.parent_section ?? null,
    subsection: sections.subsection ?? null,
    section_path: [],
    rows: null,
  }
}

describe('locateQuoteInBlocks (exact copied text over the joined blocks)', () => {
  it('finds a table-row quote at its row', () => {
    expect(locateQuoteInBlocks(blocks, '| XS0401 | 25 C |', null)?.blocks).toEqual([
      { blockIndex: 1, rowIndexes: [1] },
    ])
  })

  it('finds a quote of several rows, held in one piece, at all of them', () => {
    expect(locateQuoteInBlocks(blocks, '| Strain | Temp |\n|---|---|\n| XS0401 | 25 C |', null)?.blocks).toEqual([
      { blockIndex: 1, rowIndexes: [0, 1] },
    ])
  })

  it('finds a quote that crosses from one block into the next', () => {
    expect(locateQuoteInBlocks(blocks, '| XS0401 | 25 C |\n\nKept at two', null)?.blocks).toEqual([
      { blockIndex: 1, rowIndexes: [1] },
      { blockIndex: 2, rowIndexes: null },
    ])
  })

  it('finds a prose quote inside a block', () => {
    expect(locateQuoteInBlocks(blocks, 'two temperatures', null)?.blocks).toEqual([
      { blockIndex: 2, rowIndexes: null },
    ])
  })

  it('prefers the place in the evidence\'s section when the quote appears twice', () => {
    const repeated = [
      paragraph('Strain A grew.', { section_title: 'Methods' }),
      paragraph('Strain A grew.', { section_title: 'Table S1 > Sheet 2', parent_section: 'Table S1', subsection: 'Sheet 2' }),
    ]

    expect(locateQuoteInBlocks(repeated, 'Strain A grew.', { section: 'Table S1', subsection: 'Sheet 2' })).toEqual({
      blocks: [{ blockIndex: 1, rowIndexes: null }],
      chosenAmongSeveral: true,
    })
    expect(locateQuoteInBlocks(repeated, 'Strain A grew.', null)?.blocks).toEqual([
      { blockIndex: 0, rowIndexes: null },
    ])
  })

  it('compares exactly as served: nothing is normalised in the browser', () => {
    const served = [paragraph('The ﬁrst strain.')]

    expect(locateQuoteInBlocks(served, 'The first strain.', null)).toBeNull()
    expect(locateQuoteInBlocks(served, 'The ﬁrst strain.', null)).not.toBeNull()
  })

  // P9a's one exception: a long element cut at a sentence boundary stores one space
  // where the block keeps the original whitespace. Such a quote is not found (never a
  // wrong place), and nothing loosens whitespace to find it.
  it('does not find a quote across a sentence cut whose whitespace differs from the block', () => {
    const longElement = [paragraph(`${'A'.repeat(1500)}.  Strain XS0401 grew at 25 C.`)]

    expect(locateQuoteInBlocks(longElement, `${'A'.repeat(20)}. Strain XS0401`, null)).toBeNull()
  })

  it('returns null for a quote that is not in the file, and for an empty quote', () => {
    expect(locateQuoteInBlocks(blocks, 'Not in this file.', null)).toBeNull()
    expect(locateQuoteInBlocks(blocks, '', null)).toBeNull()
  })
})

describe('locateSectionInBlocks (the "quote not found" path)', () => {
  const sectioned = [
    paragraph('Intro.', { section_title: 'Methods' }),
    paragraph('Sheet one text.', { section_title: 'Table S1 > Sheet 1', parent_section: 'Table S1', subsection: 'Sheet 1' }),
    paragraph('Sheet two text.', { section_title: 'Table S1 > Sheet 2', parent_section: 'Table S1', subsection: 'Sheet 2' }),
  ]

  it('finds the first block whose parent section (or section title) and subsection are the evidence\'s', () => {
    expect(locateSectionInBlocks(sectioned, { section: 'Table S1', subsection: 'Sheet 2' })).toBe(2)
    expect(locateSectionInBlocks(sectioned, { section: 'Table S1', subsection: null })).toBe(1)
    expect(locateSectionInBlocks(sectioned, { section: 'Methods', subsection: null })).toBe(0)
  })

  it('finds nothing for a section the file does not have, or for evidence with no section', () => {
    expect(locateSectionInBlocks(sectioned, { section: 'Results', subsection: null })).toBeNull()
    expect(locateSectionInBlocks(sectioned, null)).toBeNull()
  })
})
