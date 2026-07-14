import { describe, expect, it } from 'vitest'

import type { AgentMetadata } from '@/services/agentStudioService'

import {
  canSourceOutputAttachmentFromMetadata,
  isExtractionAgentFromMetadata,
  isFileOutputFormatterAgentFromMetadata,
  isOutputFormatterAgentFromMetadata,
  isValidationAgentFromMetadata,
  resolveOutputFormatterIncludeEvidence,
} from './agentMetadataUtils'

const metadata: Record<string, AgentMetadata> = {
  custom_extractor: {
    name: 'Custom Extractor',
    icon: 'EX',
    category: 'Extraction',
    subcategory: 'PDF Extraction',
  },
  custom_validator: {
    name: 'Custom Validator',
    icon: 'VA',
    category: 'Entity Validation',
    subcategory: 'Data Validation',
    output_schema_key: 'validated_entities',
    is_active: true,
    produces_flow_artifacts: true,
  },
  custom_output: {
    name: 'Custom Output Formatter',
    icon: 'OUT',
    category: 'Output',
    subcategory: 'Formatter',
  },
  plain_custom: {
    name: 'Plain Custom',
    icon: 'C',
    category: 'Custom',
    subcategory: 'My Custom Agents',
    output_schema_key: 'custom_payload',
    is_active: true,
    produces_flow_artifacts: false,
  },
  inactive_validator: {
    name: 'Inactive Validator',
    icon: 'IV',
    category: 'Validation',
    subcategory: 'Data Validation',
    output_schema_key: 'validated_entities',
    is_active: false,
    produces_flow_artifacts: false,
  },
}

describe('agentMetadataUtils', () => {
  it('detects extraction agents from built-in ids or metadata', () => {
    expect(isExtractionAgentFromMetadata('pdf_extraction', metadata)).toBe(true)
    expect(isExtractionAgentFromMetadata('custom_extractor', metadata)).toBe(true)
    expect(isExtractionAgentFromMetadata('custom_validator', metadata)).toBe(false)
  })

  it('detects validation agents from built-in ids or metadata', () => {
    expect(isValidationAgentFromMetadata('gene', metadata)).toBe(true)
    expect(isValidationAgentFromMetadata('custom_validator', metadata)).toBe(true)
    expect(isValidationAgentFromMetadata('custom_output', metadata)).toBe(false)
  })

  it('accepts only output agents with a runtime formatter implementation', () => {
    expect(isOutputFormatterAgentFromMetadata('csv_formatter', metadata)).toBe(true)
    expect(isOutputFormatterAgentFromMetadata('chat_output_formatter', metadata)).toBe(true)
    expect(isOutputFormatterAgentFromMetadata('custom_output', metadata)).toBe(false)
    expect(isOutputFormatterAgentFromMetadata('custom_extractor', metadata)).toBe(false)
    expect(isOutputFormatterAgentFromMetadata('missing_agent', metadata)).toBe(false)
  })

  it('limits file naming controls to CSV, TSV, and JSON formatters', () => {
    expect(isFileOutputFormatterAgentFromMetadata('csv_formatter', metadata)).toBe(true)
    expect(isFileOutputFormatterAgentFromMetadata('tsv_formatter', metadata)).toBe(true)
    expect(isFileOutputFormatterAgentFromMetadata('json_formatter', metadata)).toBe(true)
    expect(isFileOutputFormatterAgentFromMetadata('chat_output_formatter', metadata)).toBe(false)
    expect(isFileOutputFormatterAgentFromMetadata('custom_output', metadata)).toBe(false)
  })

  it('allows extraction and typed active validation agents to source output attachments', () => {
    expect(canSourceOutputAttachmentFromMetadata('custom_extractor', metadata)).toBe(true)
    expect(canSourceOutputAttachmentFromMetadata('custom_validator', metadata)).toBe(true)
    expect(canSourceOutputAttachmentFromMetadata('plain_custom', metadata)).toBe(false)
    expect(canSourceOutputAttachmentFromMetadata('inactive_validator', metadata)).toBe(false)
    expect(canSourceOutputAttachmentFromMetadata('missing_agent', metadata)).toBe(false)
    expect(canSourceOutputAttachmentFromMetadata('custom_validator', {
      ...metadata,
      custom_validator: {
        ...metadata.custom_validator,
        produces_flow_artifacts: false,
      },
    })).toBe(false)
    expect(canSourceOutputAttachmentFromMetadata('gene', {
      gene: {
        name: 'Gene Validator',
        icon: 'G',
        category: 'Validation',
        output_schema_key: null,
      },
    })).toBe(false)
  })

  it('defaults include_evidence to true for output formatter agents', () => {
    expect(resolveOutputFormatterIncludeEvidence('csv_formatter', metadata, undefined)).toBe(true)
    expect(resolveOutputFormatterIncludeEvidence('csv_formatter', metadata, null)).toBe(true)
    expect(resolveOutputFormatterIncludeEvidence('csv_formatter', metadata, false)).toBe(false)
    expect(resolveOutputFormatterIncludeEvidence('custom_output', metadata, undefined)).toBeUndefined()
    expect(resolveOutputFormatterIncludeEvidence('custom_extractor', metadata, undefined)).toBeUndefined()
  })
})
