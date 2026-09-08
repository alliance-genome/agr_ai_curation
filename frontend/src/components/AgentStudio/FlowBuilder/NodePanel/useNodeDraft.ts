/**
 * Local draft for the node panel.
 *
 * Edits stay in this draft until the curator clicks Apply; Cancel resets the
 * draft to what the node holds. The draft covers every persisted setting the
 * panel can change, so the payload it builds preserves the flow contract:
 * instructions, task instructions, output options, file naming, the output
 * variable, and validation attachment opt-outs.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'

import type { AgentMetadata } from '@/services/agentStudioService'
import { validationAttachmentForPersistence } from '../types'
import type { AgentNode, AgentNodeData, ValidationAttachmentSelection } from '../types'
import { resolveOutputFormatterIncludeEvidence } from '../agentMetadataUtils'

export const BUILT_IN_TEMPLATE_VARIABLES = [
  'input_filename',
  'input_filename_stem',
  'trace_id',
  'timestamp',
] as const

export const SOURCE_PDF_FILENAME_TEMPLATE = '{{input_filename_stem}}'

export type OutputFilenameMode = 'source_pdf' | 'custom' | 'formatter_default'

const OUTPUT_KEY_PATTERN = /^[a-zA-Z_][a-zA-Z0-9_]*$/

export const resolveOutputFilenameMode = (template?: string): OutputFilenameMode => {
  const normalized = template?.trim()
  if (!normalized) return 'formatter_default'
  if (/^\{\{input_filename_stem\}\}(?:\.(?:csv|tsv|json))?$/i.test(normalized)) {
    return 'source_pdf'
  }
  return 'custom'
}

export const outputFileExtension = (agentId: string): 'csv' | 'tsv' | 'json' => {
  if (agentId === 'tsv_formatter') return 'tsv'
  if (agentId === 'json_formatter') return 'json'
  return 'csv'
}

export interface NodeDraftValues {
  customInstructions: string
  taskInstructions: string
  includeEvidence: boolean
  outputFilenameMode: OutputFilenameMode
  outputFilenameTemplate: string
  outputKey: string
  attachments: ValidationAttachmentSelection[]
}

export interface NodeDraftOptions {
  node: AgentNode
  agentMetadata: Record<string, AgentMetadata>
  isTaskInput: boolean
  supportsFileOutputNaming: boolean
}

export interface NodeDraft {
  values: NodeDraftValues
  dirty: boolean
  /** Human sentence for the unsaved-edits dialog, empty when clean. */
  changeSummary: string
  /** Reason Apply is not allowed right now, empty when the draft is valid. */
  blockingError: string
  set: <K extends keyof NodeDraftValues>(key: K, value: NodeDraftValues[K]) => void
  setAttachmentsEnabled: (attachmentIds: string[], enabled: boolean) => void
  reset: () => void
  /** Payload for onApply, or null when blockingError is set. */
  buildPayload: () => Partial<AgentNodeData> | null
}

function valuesFromNode(node: AgentNode, agentMetadata: Record<string, AgentMetadata>): NodeDraftValues {
  return {
    customInstructions: node.data.custom_instructions || '',
    taskInstructions: node.data.task_instructions || '',
    includeEvidence: resolveOutputFormatterIncludeEvidence(
      node.data.agent_id,
      agentMetadata,
      node.data.include_evidence,
    ) ?? false,
    outputFilenameMode: resolveOutputFilenameMode(node.data.output_filename_template),
    outputFilenameTemplate: node.data.output_filename_template || '',
    outputKey: node.data.output_key || (node.data.agent_id === 'task_input' ? 'task_input' : `${node.data.agent_id}_output`),
    attachments: node.data.validation_attachments || [],
  }
}

const enabledIds = (attachments: ValidationAttachmentSelection[]): Set<string> =>
  new Set(attachments.filter((attachment) => attachment.enabled).map((attachment) => attachment.attachment_id))

function joinPhrases(phrases: string[]): string {
  if (phrases.length <= 1) return phrases.join('')
  return `${phrases.slice(0, -1).join(', ')} and ${phrases[phrases.length - 1]}`
}

function summarizeChanges(initial: NodeDraftValues, current: NodeDraftValues, isTaskInput: boolean): string {
  const phrases: string[] = []
  if (isTaskInput) {
    if (initial.taskInstructions !== current.taskInstructions) phrases.push('changed the task instructions')
  } else if (initial.customInstructions !== current.customInstructions) {
    phrases.push('changed the instructions')
  }
  const before = enabledIds(initial.attachments)
  const after = enabledIds(current.attachments)
  const turnedOff = [...before].filter((id) => !after.has(id)).length
  const turnedOn = [...after].filter((id) => !before.has(id)).length
  if (turnedOff > 0) phrases.push(`turned off ${turnedOff === 1 ? 'one check' : `${turnedOff} checks`}`)
  if (turnedOn > 0) phrases.push(`turned on ${turnedOn === 1 ? 'one check' : `${turnedOn} checks`}`)
  if (initial.includeEvidence !== current.includeEvidence) phrases.push('changed the evidence option')
  if (
    initial.outputFilenameMode !== current.outputFilenameMode
    || initial.outputFilenameTemplate !== current.outputFilenameTemplate
  ) {
    phrases.push('changed the file name')
  }
  if (initial.outputKey !== current.outputKey) phrases.push('renamed the output variable')
  if (phrases.length === 0) return ''
  return `You ${joinPhrases(phrases)}.`
}

export function useNodeDraft({ node, agentMetadata, isTaskInput, supportsFileOutputNaming }: NodeDraftOptions): NodeDraft {
  // Keyed on the node's persisted values, not the node object: the canvas
  // hands over a new node object on every drag, and a drag must not reset a
  // draft the curator is still editing.
  const initialKey = `${node.id}\n${JSON.stringify(valuesFromNode(node, agentMetadata))}`
  const initial = useMemo(
    () => JSON.parse(initialKey.slice(initialKey.indexOf('\n') + 1)) as NodeDraftValues,
    [initialKey]
  )
  const [values, setValues] = useState<NodeDraftValues>(initial)

  // A new node, or the same node after Apply, resets the draft to what the node holds.
  useEffect(() => {
    setValues(initial)
  }, [initial])

  const dirty = useMemo(() => JSON.stringify(values) !== JSON.stringify(initial), [values, initial])
  const changeSummary = useMemo(
    () => (dirty ? summarizeChanges(initial, values, isTaskInput) : ''),
    [dirty, initial, values, isTaskInput]
  )

  const blockingError = useMemo(() => {
    if (isTaskInput && !values.taskInstructions.trim()) {
      return 'Task instructions are required.'
    }
    const key = values.outputKey.trim()
    if (!key) return 'Output variable name is required.'
    if (!OUTPUT_KEY_PATTERN.test(key)) {
      return 'Output variable must start with a letter or underscore and contain only letters, numbers, and underscores.'
    }
    if (supportsFileOutputNaming && values.outputFilenameMode === 'custom' && !values.outputFilenameTemplate.trim()) {
      return 'Enter a custom prefix before applying.'
    }
    return ''
  }, [isTaskInput, supportsFileOutputNaming, values])

  const set = useCallback(<K extends keyof NodeDraftValues>(key: K, value: NodeDraftValues[K]) => {
    setValues((current) => ({ ...current, [key]: value }))
  }, [])

  const setAttachmentsEnabled = useCallback((attachmentIds: string[], enabled: boolean) => {
    const ids = new Set(attachmentIds)
    setValues((current) => ({
      ...current,
      attachments: current.attachments.map((attachment) => (
        ids.has(attachment.attachment_id) ? { ...attachment, enabled } : attachment
      )),
    }))
  }, [])

  const reset = useCallback(() => {
    setValues(initial)
  }, [initial])

  const buildPayload = useCallback((): Partial<AgentNodeData> | null => {
    if (blockingError) return null
    const outputKey = values.outputKey.trim()
    if (isTaskInput) {
      return {
        task_instructions: values.taskInstructions.trim(),
        output_key: outputKey,
      }
    }
    const includeEvidence = agentMetadata[node.data.agent_id]
      ? resolveOutputFormatterIncludeEvidence(node.data.agent_id, agentMetadata, values.includeEvidence)
      : node.data.include_evidence
    return {
      custom_instructions: values.customInstructions || undefined,
      include_evidence: includeEvidence,
      output_filename_template: supportsFileOutputNaming
        ? values.outputFilenameMode === 'source_pdf'
          ? SOURCE_PDF_FILENAME_TEMPLATE
          : values.outputFilenameMode === 'custom'
            ? values.outputFilenameTemplate.trim() || undefined
            : undefined
        : node.data.output_filename_template,
      output_key: outputKey,
      validation_attachments: values.attachments.length > 0
        ? values.attachments.map(validationAttachmentForPersistence)
        : undefined,
    }
  }, [agentMetadata, blockingError, isTaskInput, node, supportsFileOutputNaming, values])

  return { values, dirty, changeSummary, blockingError, set, setAttachmentsEnabled, reset, buildPayload }
}
