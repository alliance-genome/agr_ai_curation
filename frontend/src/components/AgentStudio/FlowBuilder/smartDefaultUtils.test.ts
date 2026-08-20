/**
 * Unit tests for smart default utilities.
 *
 * Tests the helper functions that classify flow nodes and extractor topology.
 */

import { describe, it, expect } from 'vitest'
import {
  EXTRACTION_AGENTS,
  isExtractionAgent,
  countExtractors,
} from './smartDefaultUtils'
import type { AgentNode, AgentNodeData } from './types'

// =============================================================================
// Test Helpers
// =============================================================================

/**
 * Create a mock AgentNode for testing
 */
function createMockNode(
  id: string,
  agentId: string,
  outputKey?: string
): AgentNode {
  return {
    id,
    type: agentId === 'task_input' ? 'task_input' : 'agent',
    position: { x: 0, y: 0 },
    data: {
      agent_id: agentId,
      agent_display_name: agentId.replace(/_/g, ' ').toUpperCase(),
      output_key: outputKey || `${agentId.replace(/-/g, '_')}_output`,
    } as AgentNodeData,
  }
}

// =============================================================================
// isExtractionAgent Tests
// =============================================================================

describe('isExtractionAgent', () => {
  it('returns true for PDF agent', () => {
    expect(isExtractionAgent('pdf_extraction')).toBe(true)
  })

  it('returns true for gene_expression agent', () => {
    expect(isExtractionAgent('gene_expression')).toBe(true)
  })

  it('returns false for validation agents', () => {
    expect(isExtractionAgent('gene_validation')).toBe(false)
    expect(isExtractionAgent('allele_validation')).toBe(false)
    expect(isExtractionAgent('disease_validation')).toBe(false)
  })

  it('returns false for output agents', () => {
    expect(isExtractionAgent('chat_output')).toBe(false)
    expect(isExtractionAgent('csv_formatter')).toBe(false)
  })

  it('returns false for unknown agents', () => {
    expect(isExtractionAgent('unknown_agent')).toBe(false)
    expect(isExtractionAgent('')).toBe(false)
  })

  it('includes all expected extraction agents', () => {
    expect(EXTRACTION_AGENTS).toEqual(['pdf_extraction', 'gene_expression'])
  })
})

describe('countExtractors', () => {
  it('returns 0 for empty nodes array', () => {
    expect(countExtractors([])).toBe(0)
  })

  it('returns 0 when no extractors present', () => {
    const nodes = [
      createMockNode('node_0', 'gene_validation'),
      createMockNode('node_1', 'allele_validation'),
    ]
    expect(countExtractors(nodes)).toBe(0)
  })

  it('returns 1 for single PDF extractor', () => {
    const nodes = [
      createMockNode('node_0', 'pdf_extraction'),
      createMockNode('node_1', 'gene_validation'),
    ]
    expect(countExtractors(nodes)).toBe(1)
  })

  it('returns 1 for single gene_expression extractor', () => {
    const nodes = [
      createMockNode('node_0', 'gene_expression'),
      createMockNode('node_1', 'gene_validation'),
    ]
    expect(countExtractors(nodes)).toBe(1)
  })

  it('returns 2 for PDF + gene_expression', () => {
    const nodes = [
      createMockNode('node_0', 'pdf_extraction'),
      createMockNode('node_1', 'gene_expression'),
      createMockNode('node_2', 'gene_validation'),
    ]
    expect(countExtractors(nodes)).toBe(2)
  })

  it('counts multiple of same extractor type', () => {
    const nodes = [
      createMockNode('node_0', 'pdf_extraction'),
      createMockNode('node_1', 'pdf_extraction'),
      createMockNode('node_2', 'gene_validation'),
    ]
    expect(countExtractors(nodes)).toBe(2)
  })
})
