import { describe, expect, it } from 'vitest'

import { findAnchoredEvidenceSpan } from './textAnchoring'

describe('textAnchoring', () => {
  it('recovers an exact matching span from the page text', () => {
    const pageText = 'Alpha beta gamma delta epsilon.'
    const match = findAnchoredEvidenceSpan(pageText, 'beta gamma delta')

    expect(match).toMatchObject({
      rawQuery: 'beta gamma delta',
      coverage: 1,
      leadingAnchorMatched: true,
      trailingAnchorMatched: true,
    })
  })

  it('prefers the exact repeated occurrence nearest the selected PDF.js anchor range', () => {
    const repeatedQuote = 'beta gamma delta epsilon zeta'
    const repeatedFragment = 'gamma delta epsilon'
    const pageText = [
      'Alpha',
      repeatedQuote,
      'Bridge text that should not steal the selected occurrence.',
      repeatedQuote,
      'Omega',
    ].join(' ')
    const secondOccurrenceStart = pageText.indexOf(repeatedQuote, pageText.indexOf(repeatedQuote) + 1)
    const secondFragmentStart = pageText.indexOf(repeatedFragment, secondOccurrenceStart)

    const match = findAnchoredEvidenceSpan(pageText, repeatedQuote, {
      preferredRawRange: {
        rawStart: secondFragmentStart,
        rawEndExclusive: secondFragmentStart + repeatedFragment.length,
      },
    })

    expect(match).not.toBeNull()
    expect(match?.rawStart).toBe(secondOccurrenceStart)
    expect(match?.rawQuery).toBe(repeatedQuote)
  })

  it('uses the selected fragment occurrence when the same fragment repeats inside the quote', () => {
    const quote = 'alpha beta gamma beta gamma delta epsilon'
    const repeatedFragment = 'beta gamma'
    const pageText = [
      'Header',
      quote,
      'Bridge text between repeated quote occurrences.',
      quote,
      'Results',
    ].join(' ')
    const secondQuoteStart = pageText.indexOf(quote, pageText.indexOf(quote) + 1)
    const secondFragmentOffsetInQuote = quote.indexOf(repeatedFragment, quote.indexOf(repeatedFragment) + 1)
    const secondFragmentStart = secondQuoteStart + secondFragmentOffsetInQuote

    const match = findAnchoredEvidenceSpan(pageText, quote, {
      preferredAnchor: repeatedFragment,
      preferredRawRange: {
        rawStart: secondFragmentStart,
        rawEndExclusive: secondFragmentStart + repeatedFragment.length,
      },
    })

    expect(match).not.toBeNull()
    expect(match?.rawStart).toBe(secondQuoteStart)
    expect(match?.rawQuery).toBe(quote)
  })

  it('recovers a long scientific quote span from the PDF page text even when symbols drift', () => {
    const pageText = [
      'Actin 5C at 344 +/- 23 fmoles/eye is the most abundant among all actins,',
      'followed by Actin 87E (80 +/- 51 fmoles/eye) and Actin 57B (81 +/- 19 fmoles/eye).',
      'Higher abundance of Actin 5C in comparison to Actin 87E and Actin 57B corroborates',
      'genetic evidence indicating that amongst the six actin genes in the Drosophila genome,',
      'actin 5C is critical for photoreceptor',
    ].join(' ')
    const quote = [
      'Actin 5C at 344 ± 23 fmoles/eye is the most abundant among all actins,',
      'followed by Actin 87E (80 ± 51 fmoles/eye) and Actin 57B (81 ± 19 fmoles/eye).',
      'Higher abundance of Actin 5C in comparison to Actin 87E and Actin 57B corroborates',
      'genetic evidence indicating that amongst the six *actin* genes in the *Drosophila* genome,',
      '*actin* 5C is critical for photoreceptor',
    ].join(' ')
    const preferredAnchor = 'Higher abundance of Actin 5C in comparison to Actin 87E and Actin 57B corroborates genetic evidence'

    const match = findAnchoredEvidenceSpan(pageText, quote, { preferredAnchor })

    expect(match).not.toBeNull()
    expect(match?.rawQuery).toBe(pageText)
    expect(match?.coverage).toBeGreaterThan(0.8)
    expect(match?.includesPreferredAnchor).toBe(true)
  })

  it('rejects weak low-confidence partial overlaps', () => {
    const pageText = 'Methods and Discussion describe general protein abundance changes in several mutants.'
    const quote = 'Actin 5C is the most abundant among all actins and is critical for photoreceptor development.'

    expect(findAnchoredEvidenceSpan(pageText, quote)).toBeNull()
  })

  it('rejects spans that only align by bridging a large unrelated gap', () => {
    const pageText = [
      'all proteins changed in the allele lacking the crb_C isoform',
      'An unrelated paragraph about membrane trafficking, imaging conditions, and control experiments appears here.',
      'constitute interesting candidates in the connection of the Crumbs function in organizing the cytoskeleton',
    ].join(' ')
    const quote = [
      'all proteins changed in the allele lacking the crb_C isoform',
      'constitute interesting candidates in the connection of the Crumbs function in organizing the cytoskeleton',
    ].join(' ')

    expect(findAnchoredEvidenceSpan(pageText, quote)).toBeNull()
  })
})
