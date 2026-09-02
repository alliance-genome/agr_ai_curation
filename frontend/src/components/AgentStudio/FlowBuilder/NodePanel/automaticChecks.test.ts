import { describe, expect, it } from 'vitest'

import {
  buildDomainEnvelopeMetadata,
  buildValidationAttachmentSelection,
} from '@/test/fixtures/agentStudioDomainEnvelope'
import type { ValidationAttachmentGroup } from '../types'
import {
  buildAutomaticChecksView,
  checksHelperSentence,
  checksSummarySentence,
  customValidatorSentences,
  targetSentence,
} from './automaticChecks'

const optional = (id: string, overrides = {}) => buildValidationAttachmentSelection({
  attachment_id: id,
  validator_binding_id: `${id}_binding`,
  curator_label: `Confirm ${id}`,
  when_off: `${id} stays as written.`,
  allow_opt_out: true,
  blocking: false,
  ...overrides,
})

const locked = (id: string) => buildValidationAttachmentSelection({
  attachment_id: id,
  validator_binding_id: `${id}_binding`,
  curator_label: `Confirm ${id}`,
  when_off: undefined,
  allow_opt_out: false,
  blocking: true,
})

describe('buildAutomaticChecksView', () => {
  it('turns opt-out bindings into switches and counts locked ones', () => {
    const view = buildAutomaticChecksView(
      [optional('symbol'), locked('term'), locked('relation')],
      [],
      buildDomainEnvelopeMetadata()
    )

    expect(view.total).toBe(3)
    expect(view.alwaysRun).toBe(2)
    expect(view.optional.map((check) => check.curatorLabel)).toEqual(['Confirm symbol'])
    expect(view.turnedOff).toBe(0)
  })

  it('groups every attachment of one binding into one switch', () => {
    const view = buildAutomaticChecksView(
      [
        optional('a', { validator_binding_id: 'shared', object_type: 'gene_mention_evidence', field_path: 'gene_symbol' }),
        optional('b', { validator_binding_id: 'shared', object_type: 'gene_mention_evidence', field_path: 'other', enabled: false }),
      ],
      [],
      buildDomainEnvelopeMetadata()
    )

    expect(view.total).toBe(1)
    expect(view.optional).toHaveLength(1)
    expect(view.optional[0].attachmentIds).toEqual(['a', 'b'])
    expect(view.optional[0].enabled).toBe(false)
    expect(view.turnedOff).toBe(1)
    expect(view.optional[0].targets.map(targetSentence)).toEqual([
      'Gene mention evidence · Gene symbol',
      'Gene mention evidence · other',
    ])
  })

  it('ignores under-development and metadata-only attachments', () => {
    const view = buildAutomaticChecksView(
      [
        optional('future', { state: 'under_development' }),
        optional('metadata_only', { validator_binding_id: undefined }),
      ],
      [],
      null
    )

    expect(view.total).toBe(0)
    expect(view.optional).toEqual([])
  })

  it('counts bindings a custom validator edge replaces instead of listing them', () => {
    const groups: ValidationAttachmentGroup[] = [
      { group_id: 'g', state: 'replaced', attachment_id: 'subject', required: false, blocking: false, allow_opt_out: true },
      { group_id: 's', state: 'supplemental', required: false, blocking: false, allow_opt_out: true },
    ]
    const view = buildAutomaticChecksView([optional('subject'), locked('term')], groups, null)

    expect(view.total).toBe(1)
    expect(view.replaced).toBe(1)
    expect(view.supplemental).toBe(1)
    expect(view.optional).toEqual([])
  })

  it('names pack-scope and object-scope targets without inventing field names', () => {
    const view = buildAutomaticChecksView(
      [
        optional('pack', { scope: 'pack', object_type: undefined, field_path: undefined }),
        optional('object', { scope: 'object', field_path: undefined, affected_fields: ['gene_symbol', 'unknown_path'] }),
      ],
      [],
      buildDomainEnvelopeMetadata()
    )

    expect(view.optional[0].targets.map(targetSentence)).toEqual(['All extracted data'])
    expect(view.optional[1].targets.map(targetSentence)).toEqual([
      'Gene mention evidence · Gene symbol',
      'Gene mention evidence · unknown_path',
    ])
  })
})

describe('check sentences', () => {
  it('reads the mockup wording with the right singular and plural forms', () => {
    const nine = buildAutomaticChecksView(
      [locked('a'), locked('b'), locked('c'), optional('d'), optional('e'), optional('f'), optional('g'), optional('h'), optional('i', { enabled: false })],
      [],
      null
    )
    expect(checksSummarySentence(nine)).toBe('9 checks run on what this step extracts, 1 turned off for this flow.')
    expect(checksHelperSentence(nine)).toBe('3 checks always run. The 6 below are optional for this flow.')

    const one = buildAutomaticChecksView([locked('a')], [], null)
    expect(checksSummarySentence(one)).toBe('1 check runs on what this step extracts. It always runs; there is nothing to adjust here.')
    expect(checksHelperSentence(one)).toBe('')

    const twoLocked = buildAutomaticChecksView([locked('a'), locked('b')], [], null)
    expect(checksSummarySentence(twoLocked)).toBe('2 checks run on what this step extracts. They always run; there is nothing to adjust here.')

    const oneOptional = buildAutomaticChecksView([locked('a'), optional('b')], [], null)
    expect(checksSummarySentence(oneOptional)).toBe('2 checks run on what this step extracts.')
    expect(checksHelperSentence(oneOptional)).toBe('1 check always runs. The 1 below is optional for this flow.')

    const allOptional = buildAutomaticChecksView([optional('a'), optional('b')], [], null)
    expect(checksHelperSentence(allOptional)).toBe('The 2 below are optional for this flow.')

    expect(checksSummarySentence(buildAutomaticChecksView([], [], null))).toBe('No automatic checks run on this step.')
  })

  it('describes custom validator steps only when they exist', () => {
    expect(customValidatorSentences(buildAutomaticChecksView([], [], null))).toEqual([])
    const groups: ValidationAttachmentGroup[] = [
      { group_id: 'g', state: 'replaced', attachment_id: 'subject', required: false, blocking: false, allow_opt_out: true },
      { group_id: 's1', state: 'supplemental', required: false, blocking: false, allow_opt_out: true },
      { group_id: 's2', state: 'supplemental', required: false, blocking: false, allow_opt_out: true },
    ]
    expect(customValidatorSentences(buildAutomaticChecksView([optional('subject')], groups, null))).toEqual([
      '1 check is replaced by a custom validator step in this flow.',
      '2 custom checks are added by validator steps in this flow.',
    ])
  })
})
