import { describe, expect, it } from 'vitest'

import {
  envelopeCounts,
  envelopeObjectChoices,
  fieldTypeLabel,
  groupObjectFields,
  objectValidators,
  shortCommit,
  createProviderWordResolver,
  validatorPolicyBadge,
  validatorPolicySentence,
} from './envelopePresentation'
import {
  buildDomainEnvelopeMetadata,
  buildValidationAttachmentOption,
} from '@/test/fixtures/agentStudioDomainEnvelope'
import type { DomainEnvelopeObjectMetadata } from '@/services/agentStudioService'

function buildObject(overrides: Partial<DomainEnvelopeObjectMetadata> = {}): DomainEnvelopeObjectMetadata {
  return {
    ...buildDomainEnvelopeMetadata().object_definitions[0],
    ...overrides,
  }
}

describe('createProviderWordResolver', () => {
  it('names providers from the pack schema refs and leaves unknown keys as-is', () => {
    const word = createProviderWordResolver([
      { provider: 'provider_a', name: 'Provider A schema' },
      { provider: 'provider_b', name: 'Provider B records' },
      { provider: 'provider_a', name: 'Duplicate ignored' },
      { provider: undefined, name: 'No provider' },
    ])
    expect(word('provider_a')).toBe('Provider A schema')
    expect(word('provider_b')).toBe('Provider B records')
    expect(word('other_provider')).toBe('other_provider')
    expect(word(null)).toBe('Extractor')
    expect(word(undefined)).toBe('Extractor')
  })

  it('falls back to raw keys when the pack declares no schema refs', () => {
    const word = createProviderWordResolver([])
    expect(word('provider_a')).toBe('provider_a')
    expect(word(null)).toBe('Extractor')
  })
})

describe('fieldTypeLabel', () => {
  it('names the choice list for enum fields', () => {
    expect(fieldTypeLabel({ field_type: 'enum', enum_ref: 'PaperRole' })).toBe('choice: PaperRole')
    expect(fieldTypeLabel({ field_type: 'string', enum_ref: null })).toBe('string')
  })
})

describe('validator policy words', () => {
  it('prefers Blocking over Opt-out and returns null otherwise', () => {
    expect(validatorPolicyBadge({ blocking: true, allow_opt_out: true })).toBe('Blocking')
    expect(validatorPolicyBadge({ blocking: false, allow_opt_out: true })).toBe('Opt-out')
    expect(validatorPolicyBadge({ blocking: false, allow_opt_out: false })).toBeNull()
  })

  it('states the policy as one sentence', () => {
    expect(validatorPolicySentence({ blocking: true, allow_opt_out: false, required: false })).toMatch(/^Blocking/)
    expect(validatorPolicySentence({ blocking: false, allow_opt_out: true, required: false })).toMatch(/opt out/)
    expect(validatorPolicySentence({ blocking: false, allow_opt_out: false, required: true })).toMatch(/^Required/)
    expect(validatorPolicySentence({ blocking: false, allow_opt_out: false, required: false })).toMatch(/^Advisory/)
  })
})

describe('groupObjectFields', () => {
  const fieldA = { ...buildObject().fields[0], field_path: 'a', display_name: 'A' }
  const fieldB = { ...buildObject().fields[0], field_path: 'b', display_name: 'B' }
  const fieldC = { ...buildObject().fields[0], field_path: 'c', display_name: 'C' }

  it('returns one unlabeled group when the object declares no groups', () => {
    const groups = groupObjectFields(buildObject({ fields: [fieldA, fieldB], field_groups: [] }))
    expect(groups).toHaveLength(1)
    expect(groups[0].label).toBeNull()
    expect(groups[0].fields.map((field) => field.field_path)).toEqual(['a', 'b'])
  })

  it('follows declared group order, ignores unknown paths, and collects leftovers', () => {
    const groups = groupObjectFields(buildObject({
      fields: [fieldA, fieldB, fieldC],
      field_groups: [
        { id: 'second', label: 'Second', field_paths: ['b', 'missing'] },
        { id: 'first', label: 'First', field_paths: ['a'] },
        { id: 'empty', label: 'Empty', field_paths: ['missing'] },
      ],
    }))
    expect(groups.map((group) => group.label)).toEqual(['Second', 'First', 'Other fields'])
    expect(groups[0].fields.map((field) => field.field_path)).toEqual(['b'])
    expect(groups[2].fields.map((field) => field.field_path)).toEqual(['c'])
  })
})

describe('objectValidators', () => {
  it('lists each validator once with the number of fields it covers', () => {
    const lookup = buildValidationAttachmentOption({ validator_id: 'lookup', label: 'Lookup' })
    const lookupOnB = buildValidationAttachmentOption({ validator_id: 'lookup', label: 'Lookup', attachment_id: 'lookup:b', field_path: 'b' })
    const objectCheck = buildValidationAttachmentOption({
      validator_id: 'whole',
      label: 'Whole object check',
      attachment_id: 'whole',
      scope: 'object',
      field_path: undefined,
      state: 'under_development',
      state_explanation: 'Needs wiring.',
      blocking: false,
      allow_opt_out: false,
    })
    const base = buildObject()
    const object = buildObject({
      validation_attachments: [objectCheck],
      fields: [
        { ...base.fields[0], field_path: 'a', validation_attachments: [lookup] },
        { ...base.fields[0], field_path: 'b', validation_attachments: [lookupOnB] },
        { ...base.fields[0], field_path: 'c', validation_attachments: [] },
      ],
    })

    const validators = objectValidators(object)
    expect(validators).toHaveLength(2)
    expect(validators[0]).toMatchObject({ validatorId: 'whole', fieldCount: 0, coversWholeObject: true, state: 'under_development', stateExplanation: 'Needs wiring.' })
    expect(validators[1]).toMatchObject({ validatorId: 'lookup', fieldCount: 2, coversWholeObject: false })
    expect(validators[1].policySentence).toMatch(/^Blocking/)
  })
})

describe('envelopeObjectChoices', () => {
  it('gives curatable objects their own entry and buckets the rest as embedded references', () => {
    const base = buildObject()
    const metadata = buildDomainEnvelopeMetadata({
      object_definitions: [
        { ...base, object_type: 'annotation', display_name: 'Annotation', object_role: 'curatable_unit' },
        { ...base, object_type: 'ref_a', display_name: 'Ref A', object_role: 'validated_reference' },
        { ...base, object_type: 'ref_b', display_name: 'Ref B', object_role: 'validated_reference' },
      ],
    })
    const choices = envelopeObjectChoices(metadata)
    expect(choices.map((choice) => choice.label)).toEqual(['Annotation', 'Embedded references (2)'])
    expect(choices[1].objects.map((object) => object.object_type)).toEqual(['ref_a', 'ref_b'])
  })

  it('lists every object when none is curatable', () => {
    const choices = envelopeObjectChoices(buildDomainEnvelopeMetadata())
    expect(choices.map((choice) => choice.label)).toEqual(['Gene mention evidence'])
  })
})

describe('envelopeCounts', () => {
  it('counts distinct validators by state, required fields, and blocking checks', () => {
    const active = buildValidationAttachmentOption({ validator_id: 'v1', attachment_id: 'v1:a' })
    const activeAgain = buildValidationAttachmentOption({ validator_id: 'v1', attachment_id: 'v1:b' })
    const future = buildValidationAttachmentOption({ validator_id: 'v2', attachment_id: 'v2', state: 'under_development' })
    const metadata = buildDomainEnvelopeMetadata({
      validation_attachments: [active, activeAgain, future],
      validation_summary: { ...buildDomainEnvelopeMetadata().validation_summary, blocking: 3 },
    })
    expect(envelopeCounts(metadata)).toEqual({
      activeValidators: 1,
      underDevelopmentValidators: 1,
      requiredFields: 1,
      blockingChecks: 3,
    })
  })
})

describe('shortCommit', () => {
  it('shortens full hashes and leaves tags alone', () => {
    expect(shortCommit('1b11d0888f19eba4ca72022200bb7d96b30d4a52')).toBe('1b11d088')
    expect(shortCommit('v1.2.3')).toBe('v1.2.3')
    expect(shortCommit(undefined)).toBe('')
  })
})
