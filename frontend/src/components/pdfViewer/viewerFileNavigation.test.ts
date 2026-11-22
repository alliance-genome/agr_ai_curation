import { describe, expect, it } from 'vitest'

import { locateQuoteInBlocks } from './viewerFileNavigation'
import type { DisplayBlock } from './viewerFiles'
import { TEXT_SUPPLEMENT_BLOCKS } from './viewerFilesFixtures'

const blocks = TEXT_SUPPLEMENT_BLOCKS.blocks

function paragraph(text: string): DisplayBlock {
  return { kind: 'paragraph', text, level: null, section_path: [], rows: null }
}

describe('locateQuoteInBlocks (exact copied text over the joined blocks)', () => {
  it('finds a table-row quote at its row', () => {
    expect(locateQuoteInBlocks(blocks, '| XS0401 | 25 C |')?.blocks).toEqual([
      { blockIndex: 1, rowIndexes: [1] },
    ])
  })

  it('finds a quote of several rows, held in one piece, at all of them', () => {
    expect(locateQuoteInBlocks(blocks, '| Strain | Temp |\n|---|---|\n| XS0401 | 25 C |')?.blocks).toEqual([
      { blockIndex: 1, rowIndexes: [0, 1] },
    ])
  })

  it('finds a quote that crosses from one block into the next', () => {
    const location = locateQuoteInBlocks(blocks, '| XS0401 | 25 C |\n\nKept at two')

    expect(location?.blocks).toEqual([
      { blockIndex: 1, rowIndexes: [1] },
      { blockIndex: 2, rowIndexes: null },
    ])
  })

  it('finds a prose quote inside a block', () => {
    expect(locateQuoteInBlocks(blocks, 'two temperatures')?.blocks).toEqual([
      { blockIndex: 2, rowIndexes: null },
    ])
  })

  it('takes the first place in reading order when the quote appears twice, and says so', () => {
    const repeated = [paragraph('Strain A grew.'), paragraph('Strain A grew.')]

    expect(locateQuoteInBlocks(repeated, 'Strain A grew.')).toMatchObject({
      blocks: [{ blockIndex: 0, rowIndexes: null }],
      chosenAmongSeveral: true,
    })
  })

  it('compares exactly as served: nothing is normalised in the browser', () => {
    const served = [paragraph('The ﬁrst  strain.')]

    expect(locateQuoteInBlocks(served, 'The first strain.')).toBeNull()
    expect(locateQuoteInBlocks(served, 'The ﬁrst  strain.')).not.toBeNull()
  })

  it('returns null for a quote that is not in the file, and for an empty quote', () => {
    expect(locateQuoteInBlocks(blocks, 'Not in this file.')).toBeNull()
    expect(locateQuoteInBlocks(blocks, '')).toBeNull()
  })
})
