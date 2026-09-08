/**
 * Pure helpers behind the "Automatic checks" section of the node panel.
 *
 * Every word a curator reads about a check (the switch sentence, what it does,
 * what happens when it is off) comes from the validation attachment payload,
 * which the backend builds from the domain pack YAML. This module only counts,
 * groups, and orders; it never authors check wording of its own.
 *
 * Grouping: the envelope payload carries one attachment per object or field a
 * validator binding covers, so one binding can appear many times on a node.
 * A curator thinks of that as one check, so attachments are grouped by
 * binding and toggled together.
 *
 * Counting, not listing: blocking and locked checks have no curator control,
 * so they are counted in the summary sentences and never rendered as rows.
 * Under-development bindings do not run and are not mentioned.
 */

import type { DomainEnvelopeMetadata } from '@/services/agentStudioService'
import type { ValidationAttachmentGroup, ValidationAttachmentSelection } from '../types'

export interface CheckTarget {
  attachmentId: string
  objectType?: string
  /** Object display name from the envelope, or "All extracted data" for pack scope. */
  objectLabel: string
  fieldPath?: string
  /** Field display name from the envelope. Absent for object and pack scope. */
  fieldLabel?: string
}

export interface CheckGroupView {
  /** Validator binding id shared by every attachment in the group. */
  key: string
  attachmentIds: string[]
  /** The switch sentence, the binding's curator_label verbatim. */
  curatorLabel: string | null
  description?: string
  whenOff: string | null
  /** Every attachment in the group is enabled. */
  enabled: boolean
  /** Catalog id of the validator agent that runs the check, when the pack names one. */
  validatorAgentId?: string
  targets: CheckTarget[]
}

export interface AutomaticChecksView {
  /** Checks that run automatically on this step, turned-off ones included. */
  total: number
  /** Checks with no curator control (blocking or locked by the pack). */
  alwaysRun: number
  /** Checks the curator may turn off for this flow, in payload order. */
  optional: CheckGroupView[]
  turnedOff: number
  /** Checks a custom validator step replaces in this flow. */
  replaced: number
  /** Custom validator steps that add checks beside the automatic ones. */
  supplemental: number
}

const PACK_SCOPE_LABEL = 'All extracted data'

const isAutomatic = (attachment: ValidationAttachmentSelection): boolean =>
  attachment.state === 'active' && Boolean(attachment.validator_binding_id)

function attachmentTargets(
  attachment: ValidationAttachmentSelection,
  metadata: DomainEnvelopeMetadata | null | undefined
): CheckTarget[] {
  if (attachment.scope === 'pack' || !attachment.object_type) {
    return [{ attachmentId: attachment.attachment_id, objectLabel: PACK_SCOPE_LABEL }]
  }
  const object = metadata?.object_definitions.find(
    (candidate) => candidate.object_type === attachment.object_type
  )
  // Unknown objects fall through to their raw keys, as the Envelope tab does.
  const objectLabel = object?.display_name ?? attachment.object_type
  const fieldLabelFor = (fieldPath: string): string | undefined =>
    object?.fields.find((field) => field.field_path === fieldPath)?.display_name ?? undefined

  if (attachment.field_path) {
    return [{
      attachmentId: attachment.attachment_id,
      objectType: attachment.object_type,
      objectLabel,
      fieldPath: attachment.field_path,
      fieldLabel: fieldLabelFor(attachment.field_path),
    }]
  }

  const affected = attachment.affected_fields ?? []
  if (affected.length > 0) {
    return affected.map((fieldPath) => ({
      attachmentId: attachment.attachment_id,
      objectType: attachment.object_type,
      objectLabel,
      fieldPath,
      fieldLabel: fieldLabelFor(fieldPath),
    }))
  }

  return [{ attachmentId: attachment.attachment_id, objectType: attachment.object_type, objectLabel }]
}

/**
 * Group a node's validation attachments into the checks a curator sees.
 *
 * `groups` is the node's persisted validation_groups; a group in the
 * `replaced` state marks an attachment a custom validator edge stands in for.
 */
export function buildAutomaticChecksView(
  attachments: ValidationAttachmentSelection[],
  groups: ValidationAttachmentGroup[],
  metadata: DomainEnvelopeMetadata | null | undefined
): AutomaticChecksView {
  const replacedAttachmentIds = new Set(
    groups.filter((group) => group.state === 'replaced' && group.attachment_id).map((group) => group.attachment_id as string)
  )
  const automatic = attachments.filter(isAutomatic)

  const byBinding = new Map<string, ValidationAttachmentSelection[]>()
  automatic.forEach((attachment) => {
    const key = attachment.validator_binding_id as string
    const bucket = byBinding.get(key)
    if (bucket) bucket.push(attachment)
    else byBinding.set(key, [attachment])
  })

  let total = 0
  let alwaysRun = 0
  let replaced = 0
  let turnedOff = 0
  const optional: CheckGroupView[] = []

  byBinding.forEach((members, key) => {
    const live = members.filter((member) => !replacedAttachmentIds.has(member.attachment_id))
    if (live.length === 0) {
      replaced += 1
      return
    }
    total += 1
    const first = live[0]
    if (!first.allow_opt_out) {
      alwaysRun += 1
      return
    }
    const enabled = live.every((member) => member.enabled)
    if (!enabled) turnedOff += 1
    optional.push({
      key,
      attachmentIds: live.map((member) => member.attachment_id),
      curatorLabel: first.curator_label,
      description: first.description,
      whenOff: first.when_off,
      enabled,
      validatorAgentId: first.validator_agent_id,
      targets: live.flatMap((member) => attachmentTargets(member, metadata)),
    })
  })

  const supplemental = groups.filter((group) => group.state === 'supplemental').length

  return { total, alwaysRun, optional, turnedOff, replaced, supplemental }
}

const plural = (count: number, singular: string, pluralWord: string): string =>
  `${count} ${count === 1 ? singular : pluralWord}`

/** First sentence under the "Automatic checks" heading. */
export function checksSummarySentence(view: AutomaticChecksView): string {
  if (view.total === 0) return 'No automatic checks run on this step.'
  const verb = view.total === 1 ? 'runs' : 'run'
  const lead = `${plural(view.total, 'check', 'checks')} ${verb} on what this step extracts`
  if (view.turnedOff > 0) {
    return `${lead}, ${view.turnedOff} turned off for this flow.`
  }
  if (view.optional.length === 0) {
    const pronoun = view.total === 1 ? 'It always runs' : 'They always run'
    return `${lead}. ${pronoun}; there is nothing to adjust here.`
  }
  return `${lead}.`
}

/** Helper line naming how many checks always run and how many are optional. Empty when there is nothing optional. */
export function checksHelperSentence(view: AutomaticChecksView): string {
  if (view.optional.length === 0) return ''
  const optionalCount = view.optional.length
  const optionalPart = optionalCount === 1
    ? 'The 1 below is optional for this flow.'
    : `The ${optionalCount} below are optional for this flow.`
  if (view.alwaysRun === 0) return optionalPart
  const alwaysVerb = view.alwaysRun === 1 ? 'runs' : 'run'
  return `${plural(view.alwaysRun, 'check', 'checks')} always ${alwaysVerb}. ${optionalPart}`
}

/** Sentences about custom validator steps, one per situation that applies. */
export function customValidatorSentences(view: AutomaticChecksView): string[] {
  const sentences: string[] = []
  if (view.replaced > 0) {
    sentences.push(
      `${plural(view.replaced, 'check is', 'checks are')} replaced by a custom validator step in this flow.`
    )
  }
  if (view.supplemental > 0) {
    sentences.push(
      `${plural(view.supplemental, 'custom check is', 'custom checks are')} added by validator steps in this flow.`
    )
  }
  return sentences
}

export function targetSentence(target: CheckTarget): string {
  if (target.fieldPath) {
    return `${target.objectLabel} · ${target.fieldLabel ?? target.fieldPath}`
  }
  return target.objectLabel
}
