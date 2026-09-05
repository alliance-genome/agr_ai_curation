import { sha256 as hashSha256 } from '@noble/hashes/sha2.js'
import type { AgentWorkshopContext, ChatContext, FlowContextDefinition } from '@/types/promptExplorer'

type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue }

const textEncoder = new TextEncoder()

function compareUtf8(left: string, right: string): number {
  const leftBytes = textEncoder.encode(left)
  const rightBytes = textEncoder.encode(right)
  const length = Math.min(leftBytes.length, rightBytes.length)
  for (let index = 0; index < length; index += 1) {
    if (leftBytes[index] !== rightBytes[index]) return leftBytes[index] - rightBytes[index]
  }
  return leftBytes.length - rightBytes.length
}

function canonicalNumber(value: number, rejectNonFinite: boolean): JsonValue {
  if (rejectNonFinite && !Number.isFinite(value)) {
    throw new TypeError('Draft fingerprints require finite numbers')
  }
  const transportValue = Object.is(value, -0) ? 0 : value
  const buffer = new ArrayBuffer(8)
  new DataView(buffer).setFloat64(0, transportValue, false)
  const hex = Array.from(new Uint8Array(buffer), (byte) => byte.toString(16).padStart(2, '0')).join('')
  return { __authoring_float64__: hex }
}

function canonicalize(value: unknown, rejectNonFinite = false): JsonValue {
  if (typeof value === 'number') return canonicalNumber(value, rejectNonFinite)
  if (value === null || typeof value === 'boolean' || typeof value === 'string') {
    return value
  }
  if (Array.isArray(value)) {
    return value.map((entry) => canonicalize(entry, rejectNonFinite))
  }
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>
    return Object.keys(record)
      .filter((key) => record[key] !== undefined)
      .sort(compareUtf8)
      .reduce<Record<string, JsonValue>>((result, key) => {
        result[key] = canonicalize(record[key], rejectNonFinite)
        return result
      }, {})
  }
  throw new TypeError(`Unsupported draft fingerprint value: ${typeof value}`)
}

export function canonicalAuthoringJson(value: unknown): string {
  return JSON.stringify(canonicalize(value))
}

async function sha256(value: unknown): Promise<string> {
  const bytes = textEncoder.encode(JSON.stringify(canonicalize(value, true)))
  // Draft authoring also runs on HTTP dev hosts, where crypto.subtle is unavailable.
  const digest = hashSha256(bytes)
  const hex = Array.from(digest, (byte) => byte.toString(16).padStart(2, '0')).join('')
  return `sha256:${hex}`
}

function normalizeFlowDefinition(definition: FlowContextDefinition): FlowContextDefinition {
  return {
    ...definition,
    nodes: [...definition.nodes]
      .map((node) => ({
        ...node,
        validation_attachments: node.validation_attachments
          ? [...node.validation_attachments].sort((left, right) => (
              compareUtf8(String(left.attachment_id ?? ''), String(right.attachment_id ?? ''))
            ))
          : undefined,
        validation_groups: node.validation_groups
          ? [...node.validation_groups].sort((left, right) => (
              compareUtf8(String(left.group_id ?? ''), String(right.group_id ?? ''))
            ))
          : undefined,
      }))
      .sort((left, right) => compareUtf8(left.id, right.id)),
    edges: [...definition.edges].sort((left, right) => compareUtf8(left.id, right.id)),
  }
}

export async function fingerprintFlowDraft(context: ChatContext): Promise<string> {
  if (!context.flow_definition) {
    throw new TypeError('Cannot fingerprint a missing flow draft')
  }
  return sha256({
    version: 1,
    artifact_kind: 'flow',
    artifact_id: context.flow_id ?? null,
    baseline_updated_at: context.flow_updated_at ?? null,
    draft: {
      name: context.flow_name ?? '',
      description: context.flow_description ?? '',
      definition: normalizeFlowDefinition(context.flow_definition),
    },
  })
}

function workshopAuthorableDraft(workshop: AgentWorkshopContext) {
  return {
    getting_started_mode: workshop.getting_started_mode ?? 'scratch',
    template_source: workshop.template_source ?? null,
    clone_source_agent_id: workshop.clone_source_agent_id ?? null,
    clone_source_updated_at: workshop.clone_source_updated_at ?? null,
    name: workshop.draft_name ?? '',
    description: workshop.draft_description ?? '',
    icon: workshop.draft_icon ?? '',
    visibility: workshop.draft_visibility ?? 'private',
    allowed_group_ids: [...(workshop.draft_allowed_group_ids ?? [])].sort(compareUtf8),
    inherited_allowed_group_ids: [...(workshop.inherited_allowed_group_ids ?? [])].sort(compareUtf8),
    prompt: workshop.prompt_draft ?? '',
    group_prompt_overrides: workshop.group_prompt_overrides ?? {},
    include_group_rules: workshop.include_group_rules ?? false,
    model_id: workshop.draft_model_id ?? '',
    model_reasoning: workshop.draft_model_reasoning ?? '',
    tool_ids: [...(workshop.draft_tool_ids ?? [])].sort(compareUtf8),
    output_schema_key: workshop.draft_output_schema_key ?? '',
    output_draft: workshop.draft_output ?? null,
  }
}

export async function fingerprintWorkshopDraft(workshop: AgentWorkshopContext): Promise<string> {
  return sha256({
    version: 1,
    artifact_kind: 'custom_agent',
    artifact_id: workshop.custom_agent_id ?? null,
    baseline_updated_at: workshop.custom_agent_updated_at ?? null,
    draft: workshopAuthorableDraft(workshop),
  })
}

/** Synchronous equality token for guarding awaits; uses only fingerprinted state. */
export function workshopDraftKey(workshop: AgentWorkshopContext): string {
  return canonicalAuthoringJson({
    artifact_id: workshop.custom_agent_id ?? null,
    baseline_updated_at: workshop.custom_agent_updated_at ?? null,
    draft: workshopAuthorableDraft(workshop),
  })
}

/**
 * Add deterministic stale-edit tokens after the editor values have been copied.
 * Callers capture synchronously first, then await this hashing work.
 */
export async function fingerprintAuthoringContext(captured: ChatContext): Promise<ChatContext> {
  const context: ChatContext = {
    ...captured,
    flow_definition: captured.flow_definition
      ? normalizeFlowDefinition(captured.flow_definition)
      : undefined,
    agent_workshop: captured.agent_workshop
      ? {
          ...captured.agent_workshop,
          draft_allowed_group_ids: [...(captured.agent_workshop.draft_allowed_group_ids ?? [])].sort(compareUtf8),
          inherited_allowed_group_ids: [...(captured.agent_workshop.inherited_allowed_group_ids ?? [])].sort(compareUtf8),
          draft_tool_ids: [...(captured.agent_workshop.draft_tool_ids ?? [])].sort(compareUtf8),
        }
      : undefined,
  }

  const [flowFingerprint, workshopFingerprint] = await Promise.all([
    context.flow_definition ? fingerprintFlowDraft(context) : Promise.resolve(undefined),
    context.agent_workshop ? fingerprintWorkshopDraft(context.agent_workshop) : Promise.resolve(undefined),
  ])

  return {
    ...context,
    flow_draft_fingerprint: flowFingerprint,
    agent_workshop: context.agent_workshop
      ? { ...context.agent_workshop, draft_fingerprint: workshopFingerprint }
      : undefined,
  }
}
