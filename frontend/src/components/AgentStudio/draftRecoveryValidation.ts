/** Validate browser data before it reaches render/state code. Empty authored values remain valid. */
type Check = (value: unknown) => boolean
const string: Check = value => typeof value === 'string'
const boolean: Check = value => typeof value === 'boolean'
const number: Check = value => typeof value === 'number' && Number.isFinite(value)
const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value)
const optional = (check: Check): Check => value => value === undefined || check(value)
const nullable = (check: Check): Check => value => value === null || check(value)
const array = (check: Check): Check => value => Array.isArray(value) && value.every(check)
const record = (check: Check): Check => value => object(value) && Object.values(value).every(check)
const oneOf = (...values: unknown[]): Check => value => values.includes(value)
const shape = (fields: Record<string, Check>): Check => value => object(value) && Object.entries(fields).every(([key, check]) => check(value[key]))
const strings = array(string)
const maybeString = optional(nullable(string))
const ref = shape({ package_id: string, agent_id: string, domain_pack_id: string })
const pin = shape({ profile_id: string, profile_revision_id: string, revision: number, fingerprint: string })
const schema: Check = value => object(value) && (
  ['string', 'integer', 'number', 'boolean'].includes(String(value.kind))
  || (value.kind === 'enum' && strings(value.values))
  || (value.kind === 'array' && schema(value.items))
  || (value.kind === 'object' && array(field)(value.fields))
)
const field: Check = shape({ key: string, display_name: optional(string), description: optional(string),
  required: optional(boolean), nullable: optional(boolean), source_labels: optional(strings), value_schema: schema })
const mapping = shape({ mapping_id: string, capability_fingerprint: string,
  capability_ref: shape({ package_id: string, package_version: string, domain_pack_id: string, domain_pack_version: string, binding_id: string }),
  inputs: record(shape({ source: optional(oneOf('field', 'constant', 'context')), field_path: maybeString })),
  outputs: record(string), policy: shape({ unresolved: oneOf('informational', 'requires_curator_review', 'error'), blocks_readiness: boolean }),
  mode: optional(oneOf('whole', 'per_element')) })
const profile = shape({ name: string, description: optional(string), semantic_class: string, fields: array(field), validator_mappings: optional(array(mapping)) })
const output = shape({ mode: oneOf('none', 'domain', 'profile_bound_generic', 'unprofiled_generic'), schemaKey: string,
  domainExtractionRef: optional(ref), profilePin: nullable(pin), profileContract: nullable(profile) })
const fields = shape({ name: string, description: string, customPrompt: string, groupPromptOverrides: record(string),
  includeGroupRules: boolean, visibility: oneOf('private', 'project'), allowedGroupIds: strings, modelId: string,
  defaultExportExecutionMode: optional(oneOf('ai', 'direct')), modelReasoning: string, toolIds: strings, outputDraft: output, icon: string })
export const isWorkshopRecovery = shape({ fields, baseline: nullable(fields), mode: oneOf('scratch', 'template', 'clone'),
  parentId: string, customId: string, cloneId: string, sourceUpdatedAt: optional(string), cloneUpdatedAt: optional(string) })
const outputContract = shape({ output_state: oneOf('none', 'structured_extraction'),
  output_mode: optional(nullable(oneOf('domain', 'profile_bound_generic', 'unprofiled_generic'))),
  output_schema_key: maybeString, generic_profile_ref: optional(nullable(pin)), domain_extraction_ref: optional(nullable(ref)) })
const receipt = shape({ agent_id: string, agent_key: string, agent_revision_id: string, revision: number, fingerprint: string, output_contract: outputContract })
const attachment = shape({ attachment_id: string, domain_pack_id: string, validator_id: string, label: string,
  enabled: boolean, required: boolean, blocking: boolean, default_enabled: boolean, allow_opt_out: boolean,
  state: oneOf('active', 'under_development'), scope: oneOf('pack', 'object', 'field'),
  curator_label: maybeString, when_off: maybeString, affected_fields: optional(strings), unavailable_reasons: optional(strings) })
const nodeData = shape({ agent_id: string, agent_display_name: string, output_key: string,
  agent_description: maybeString, agent_revision_id: maybeString, execution_receipt: optional(nullable(receipt)),
  task_instructions: maybeString, step_goal: maybeString, custom_instructions: maybeString,
  prompt_version: optional(nullable(number)), include_evidence: optional(nullable(boolean)), output_filename_template: maybeString,
  export_execution_mode: optional(oneOf('ai', 'direct')), projection_plan: optional(nullable(object)), validation_attachments: optional(array(attachment)) })
const definition = shape({ version: oneOf('1.1'), entry_node_id: string, task_instructions_default_only: optional(nullable(boolean)),
  nodes: array(shape({ id: string, type: oneOf('agent', 'decision', 'output', 'task_input'), position: shape({ x: number, y: number }), data: nodeData })),
  edges: array(shape({ id: string, source: string, target: string, role: optional(oneOf('control_flow', 'output_attachment', 'validation_attachment')),
    satisfies_binding_id: maybeString, replaces_attachment_id: maybeString,
    condition: optional(nullable(shape({ type: oneOf('contains', 'not_empty', 'matches_pattern'), value: maybeString }))) })) })
export const isFlowRecovery = shape({ currentFlowId: nullable(string), draft: shape({ name: string, description: string, definition }) })
