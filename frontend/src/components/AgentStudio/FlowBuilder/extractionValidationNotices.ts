import type { AgentMetadata } from '@/services/agentStudioService'
import type { FlowDefinition } from './types'
import { canonicalAuthoringJson } from '../authoringContext'
import { isExtractionAgentFromMetadata, isOutputFormatterAgentFromMetadata, isValidationAgentFromMetadata } from './agentMetadataUtils'

export interface ExtractionValidationNotice {
  nodeId: string
  label: string
  configuration: string
  hasValidator: boolean
}

/** Configuration information only: a connected check does not mean verified results. */
export function extractionValidationNotices(definition: FlowDefinition, metadata: Record<string, AgentMetadata>, validatorSchemaKeys: readonly string[] = []): ExtractionValidationNotice[] {
  const isValidator = (node: FlowDefinition['nodes'][number]) => {
    const receipt = node.data.execution_receipt
    const schema = receipt ? receipt.output_contract.output_schema_key : metadata[node.data.agent_id]?.output_schema_key
    return Boolean(schema && validatorSchemaKeys.includes(schema))
      || (!receipt && isValidationAgentFromMetadata(node.data.agent_id, metadata))
  }
  return definition.nodes.flatMap(node => {
    const data = node.data
    const agent = metadata[data.agent_id]
    const output = data.execution_receipt?.output_contract
    if (node.type !== 'agent' || (!data.execution_receipt && isOutputFormatterAgentFromMetadata(data.agent_id, metadata))
      || isValidator(node) || output?.output_state === 'none') return []
    // Saved output intent also identifies custom extractors without guessing from field names.
    if (output?.output_state !== 'structured_extraction' && !isExtractionAgentFromMetadata(data.agent_id, metadata)) return []
    // A pinned revision's selections are authoritative; never substitute the current agent head.
    if (data.execution_receipt && data.validation_attachments === undefined) return []
    if (!data.execution_receipt && !agent && data.validation_attachments === undefined) return []
    const checks = (data.validation_attachments ?? agent?.validation_attachments ?? [])
      .filter(check => check.state === 'active' && check.validator_binding_id)
      .map(check => ({ binding: check.validator_binding_id!, enabled: 'enabled' in check ? check.enabled : check.default_enabled }))
      .sort((a, b) => a.binding.localeCompare(b.binding))
    const connections = definition.edges.filter(edge => edge.source === node.id && edge.role === 'validation_attachment')
      .flatMap(edge => {
        const target = definition.nodes.find(candidate => candidate.id === edge.target)
        if (!target || target.type !== 'agent' || !isValidator(target)) return []
        const binding = edge.satisfies_binding_id ?? data.validation_attachments?.find(check => check.attachment_id === edge.replaces_attachment_id)?.validator_binding_id
        return binding ? [{ binding, target: target.id, agent: target.data.agent_id }] : []
      }).sort((a, b) => canonicalAuthoringJson(a).localeCompare(canonicalAuthoringJson(b)))
    return [{
      nodeId: node.id, label: data.agent_display_name,
      hasValidator: checks.some(check => check.enabled) || connections.length > 0,
      configuration: canonicalAuthoringJson({ agent: data.agent_id, output: output ?? {
        schema: agent?.output_schema_key ?? null, domain: agent?.domain_extraction_ref ?? null,
      }, checks, connections }),
    }]
  })
}

export function noticeStorageKey(owner: string | undefined, flow: string): string | null {
  return owner ? `agr-flow-validation-notices:v1:${encodeURIComponent(owner)}:${encodeURIComponent(flow)}` : null
}

export function copyNoticeDismissals(owner: string | undefined, from: string, to: string): void {
  const source = noticeStorageKey(owner, from)
  const destination = noticeStorageKey(owner, to)
  if (!source || !destination || source === destination) return
  try {
    const value = localStorage.getItem(source)
    if (value !== null) localStorage.setItem(destination, value)
  } catch { /* Browser preferences must never prevent saving a flow. */ }
}
