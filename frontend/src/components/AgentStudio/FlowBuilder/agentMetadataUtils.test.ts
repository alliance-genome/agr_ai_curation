import { describe, expect, it } from 'vitest'

import type { AgentMetadata } from '@/services/agentStudioService'

import {
  isExtractionAgentFromMetadata,
  isOutputFormatterAgentFromMetadata,
  isValidationAgentFromMetadata,
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
  },
  custom_output: {
    name: 'Custom Output Formatter',
    icon: 'OUT',
    category: 'Output',
    subcategory: 'Formatter',
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

  it('detects output formatter agents from metadata categories', () => {
    expect(isOutputFormatterAgentFromMetadata('custom_output', metadata)).toBe(true)
    expect(isOutputFormatterAgentFromMetadata('custom_extractor', metadata)).toBe(false)
    expect(isOutputFormatterAgentFromMetadata('missing_agent', metadata)).toBe(false)
  })
})
