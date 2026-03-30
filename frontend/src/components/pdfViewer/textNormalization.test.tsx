import { describe, expect, it } from 'vitest'

import {
  buildNormalizedTextSourceMap,
  extractSentenceCandidate,
  normalizeTextForEvidenceMatch,
  sanitizeEvidenceSearchText,
  splitNormalizedWords,
} from './textNormalization'

describe('textNormalization', () => {
  it('applies the canonical frontend normalization contract', () => {
    expect(
      normalizeTextForEvidenceMatch('“Line\u00a0one”\n  and — line\u00adtwo , ( spaced )'),
    ).toBe('"Line one" and - linetwo, (spaced)')
  })

  it('collapses whitespace runs and trims accidental bracket and punctuation spacing', () => {
    expect(
      normalizeTextForEvidenceMatch('  Alpha\t\tbeta  ;  [ gamma ]  { delta }  '),
    ).toBe('Alpha beta; [gamma] {delta}')
  })

  it('preserves source indexes for normalized text-layer matching', () => {
    const source = 'Alpha\u00a0beta , ( gamma )'
    const map = buildNormalizedTextSourceMap(source)

    expect(map.text).toBe('Alpha beta, (gamma)')
    expect(map.sourceIndices).toHaveLength(map.text.length)
    expect(map.sourceIndices[0]).toBe(0)
    expect(map.sourceIndices.at(-1)).toBe(source.indexOf(')'))
  })

  it('derives deterministic sentence and fragment helpers from normalized text', () => {
    const input = 'First sentence stays intact after normalization. Second sentence continues with extra words.'

    expect(extractSentenceCandidate(input)).toBe('First sentence stays intact after normalization.')
    expect(splitNormalizedWords('Alpha\u00a0beta\n gamma')).toEqual(['Alpha', 'beta', 'gamma'])
  })

  it('strips lightweight markdown wrappers and ellipsis from evidence search text', () => {
    expect(
      sanitizeEvidenceSearchText(
        'all proteins changed in the allele lacking the *crb_C* isoform … in the connection of the `Crumbs` function',
      ),
    ).toBe(
      'all proteins changed in the allele lacking the crb_C isoform   in the connection of the Crumbs function',
    )
  })
})
