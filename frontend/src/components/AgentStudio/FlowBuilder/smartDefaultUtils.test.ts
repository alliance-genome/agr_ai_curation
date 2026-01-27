/**
 * Unit tests for smart default utilities.
 *
 * Tests the helper functions that determine how validators should
 * default to using extractor output instead of previous validator output.
 */

import { describe, it, expect } from 'vitest'
import {
  EXTRACTION_AGENTS,
  VALIDATION_AGENTS,
  isExtractionAgent,
  isValidationAgent,
  findNearestExtractor,
  countExtractors,
  getExtractors,
  validatorHasExplicitExtractorInput,
  validatorNeedsConfiguration,
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
      input_source: 'custom',
      output_key: outputKey || `${agentId}_output`,
    } as AgentNodeData,
  }
}

// =============================================================================
// isExtractionAgent Tests
// =============================================================================

describe('isExtractionAgent', () => {
  it('returns true for PDF agent', () => {
    expect(isExtractionAgent('pdf')).toBe(true)
  })

  it('returns true for gene_expression agent', () => {
    expect(isExtractionAgent('gene_expression')).toBe(true)
  })

  it('returns false for validation agents', () => {
    expect(isExtractionAgent('gene')).toBe(false)
    expect(isExtractionAgent('allele')).toBe(false)
    expect(isExtractionAgent('disease')).toBe(false)
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
    expect(EXTRACTION_AGENTS).toEqual(['pdf', 'gene_expression'])
  })
})

// =============================================================================
// isValidationAgent Tests
// =============================================================================

describe('isValidationAgent', () => {
  it('returns true for gene agent', () => {
    expect(isValidationAgent('gene')).toBe(true)
  })

  it('returns true for allele agent', () => {
    expect(isValidationAgent('allele')).toBe(true)
  })

  it('returns true for disease agent', () => {
    expect(isValidationAgent('disease')).toBe(true)
  })

  it('returns true for chemical agent', () => {
    expect(isValidationAgent('chemical')).toBe(true)
  })

  it('returns true for gene_ontology agent', () => {
    expect(isValidationAgent('gene_ontology')).toBe(true)
  })

  it('returns true for go_annotations agent', () => {
    expect(isValidationAgent('go_annotations')).toBe(true)
  })

  it('returns true for orthologs agent', () => {
    expect(isValidationAgent('orthologs')).toBe(true)
  })

  it('returns true for ontology_mapping agent', () => {
    expect(isValidationAgent('ontology_mapping')).toBe(true)
  })

  it('returns false for extraction agents', () => {
    expect(isValidationAgent('pdf')).toBe(false)
    expect(isValidationAgent('gene_expression')).toBe(false)
  })

  it('returns false for output agents', () => {
    expect(isValidationAgent('chat_output')).toBe(false)
    expect(isValidationAgent('csv_formatter')).toBe(false)
  })

  it('returns false for unknown agents', () => {
    expect(isValidationAgent('unknown_agent')).toBe(false)
    expect(isValidationAgent('')).toBe(false)
  })

  it('includes all expected validation agents', () => {
    expect(VALIDATION_AGENTS).toEqual([
      'gene', 'allele', 'disease', 'chemical',
      'gene_ontology', 'go_annotations', 'orthologs', 'ontology_mapping'
    ])
  })
})

// =============================================================================
// findNearestExtractor Tests
// =============================================================================

describe('findNearestExtractor', () => {
  describe('with direct connection', () => {
    it('finds extractor directly connected to target', () => {
      const nodes = [
        createMockNode('node_0', 'pdf', 'pdf_output'),
        createMockNode('node_1', 'gene'),
      ]
      const edges = [{ source: 'node_0', target: 'node_1' }]

      const result = findNearestExtractor('node_1', nodes, edges)

      expect(result).not.toBeNull()
      expect(result?.id).toBe('node_0')
      expect(result?.data.agent_id).toBe('pdf')
    })
  })

  describe('with multi-hop connection', () => {
    it('finds extractor through intermediate node', () => {
      // task_input -> pdf -> gene -> allele
      const nodes = [
        createMockNode('node_0', 'task_input', 'task_input'),
        createMockNode('node_1', 'pdf', 'pdf_output'),
        createMockNode('node_2', 'gene', 'gene_output'),
        createMockNode('node_3', 'allele', 'allele_output'),
      ]
      const edges = [
        { source: 'node_0', target: 'node_1' },
        { source: 'node_1', target: 'node_2' },
        { source: 'node_2', target: 'node_3' },
      ]

      const result = findNearestExtractor('node_3', nodes, edges)

      expect(result).not.toBeNull()
      expect(result?.id).toBe('node_1')
      expect(result?.data.agent_id).toBe('pdf')
    })
  })

  describe('with multiple extractors', () => {
    it('finds nearest extractor in chain', () => {
      // Two extractors in sequence (unusual but possible)
      // pdf -> gene_expression -> gene
      const nodes = [
        createMockNode('node_0', 'pdf', 'pdf_output'),
        createMockNode('node_1', 'gene_expression', 'gex_output'),
        createMockNode('node_2', 'gene', 'gene_output'),
      ]
      const edges = [
        { source: 'node_0', target: 'node_1' },
        { source: 'node_1', target: 'node_2' },
      ]

      const result = findNearestExtractor('node_2', nodes, edges)

      expect(result).not.toBeNull()
      expect(result?.id).toBe('node_1') // gene_expression is nearer
      expect(result?.data.agent_id).toBe('gene_expression')
    })
  })

  describe('with no extractors', () => {
    it('returns null when no extractors exist', () => {
      const nodes = [
        createMockNode('node_0', 'task_input'),
        createMockNode('node_1', 'gene'),
        createMockNode('node_2', 'allele'),
      ]
      const edges = [
        { source: 'node_0', target: 'node_1' },
        { source: 'node_1', target: 'node_2' },
      ]

      const result = findNearestExtractor('node_2', nodes, edges)

      expect(result).toBeNull()
    })
  })

  describe('with disconnected validator', () => {
    it('falls back to most recent extractor in graph', () => {
      // pdf and gene exist but are not connected
      const nodes = [
        createMockNode('node_0', 'pdf', 'pdf_output'),
        createMockNode('node_1', 'gene'), // Not connected to pdf
      ]
      const edges: { source: string; target: string }[] = []

      const result = findNearestExtractor('node_1', nodes, edges)

      expect(result).not.toBeNull()
      expect(result?.id).toBe('node_0')
      expect(result?.data.agent_id).toBe('pdf')
    })

    it('returns most recently added extractor when multiple exist disconnected', () => {
      // Two extractors exist, neither connected to target
      const nodes = [
        createMockNode('node_0', 'pdf', 'pdf_output'),
        createMockNode('node_5', 'gene_expression', 'gex_output'),
        createMockNode('node_10', 'gene'),
      ]
      const edges: { source: string; target: string }[] = []

      const result = findNearestExtractor('node_10', nodes, edges)

      expect(result).not.toBeNull()
      expect(result?.id).toBe('node_5') // Higher node number = more recent
      expect(result?.data.agent_id).toBe('gene_expression')
    })
  })

  describe('with cyclic graph', () => {
    it('handles cycles without infinite loop', () => {
      // Create a cycle: node_1 -> node_2 -> node_3 -> node_1
      const nodes = [
        createMockNode('node_0', 'pdf', 'pdf_output'),
        createMockNode('node_1', 'gene'),
        createMockNode('node_2', 'allele'),
        createMockNode('node_3', 'disease'),
      ]
      const edges = [
        { source: 'node_0', target: 'node_1' },
        { source: 'node_1', target: 'node_2' },
        { source: 'node_2', target: 'node_3' },
        { source: 'node_3', target: 'node_1' }, // Cycle back
      ]

      // Should find extractor without hanging
      const result = findNearestExtractor('node_3', nodes, edges)

      expect(result).not.toBeNull()
      expect(result?.id).toBe('node_0')
    })
  })

  describe('with empty graph', () => {
    it('returns null for empty nodes array', () => {
      const result = findNearestExtractor('node_1', [], [])
      expect(result).toBeNull()
    })

    it('falls back to any extractor when target not in graph', () => {
      const nodes = [createMockNode('node_0', 'pdf')]
      const result = findNearestExtractor('nonexistent', nodes, [])
      // Falls back to finding any extractor in the graph
      expect(result).not.toBeNull()
      expect(result?.id).toBe('node_0')
    })
  })

  describe('with multiple incoming edges', () => {
    it('finds extractor through any incoming path', () => {
      // Two paths to target, one has extractor
      // node_0 (pdf) -> node_2 (gene)
      // node_1 (task_input) -> node_2 (gene)
      const nodes = [
        createMockNode('node_0', 'pdf', 'pdf_output'),
        createMockNode('node_1', 'task_input'),
        createMockNode('node_2', 'gene'),
      ]
      const edges = [
        { source: 'node_0', target: 'node_2' },
        { source: 'node_1', target: 'node_2' },
      ]

      const result = findNearestExtractor('node_2', nodes, edges)

      expect(result).not.toBeNull()
      expect(result?.data.agent_id).toBe('pdf')
    })
  })
})

// =============================================================================
// countExtractors Tests
// =============================================================================

describe('countExtractors', () => {
  it('returns 0 for empty nodes array', () => {
    expect(countExtractors([])).toBe(0)
  })

  it('returns 0 when no extractors present', () => {
    const nodes = [
      createMockNode('node_0', 'gene'),
      createMockNode('node_1', 'allele'),
    ]
    expect(countExtractors(nodes)).toBe(0)
  })

  it('returns 1 for single PDF extractor', () => {
    const nodes = [
      createMockNode('node_0', 'pdf'),
      createMockNode('node_1', 'gene'),
    ]
    expect(countExtractors(nodes)).toBe(1)
  })

  it('returns 1 for single gene_expression extractor', () => {
    const nodes = [
      createMockNode('node_0', 'gene_expression'),
      createMockNode('node_1', 'gene'),
    ]
    expect(countExtractors(nodes)).toBe(1)
  })

  it('returns 2 for PDF + gene_expression', () => {
    const nodes = [
      createMockNode('node_0', 'pdf'),
      createMockNode('node_1', 'gene_expression'),
      createMockNode('node_2', 'gene'),
    ]
    expect(countExtractors(nodes)).toBe(2)
  })

  it('counts multiple of same extractor type', () => {
    const nodes = [
      createMockNode('node_0', 'pdf'),
      createMockNode('node_1', 'pdf'),
      createMockNode('node_2', 'gene'),
    ]
    expect(countExtractors(nodes)).toBe(2)
  })
})

// =============================================================================
// getExtractors Tests
// =============================================================================

describe('getExtractors', () => {
  it('returns empty array for no extractors', () => {
    const nodes = [
      createMockNode('node_0', 'gene'),
      createMockNode('node_1', 'allele'),
    ]
    expect(getExtractors(nodes)).toEqual([])
  })

  it('returns all extraction agents', () => {
    const nodes = [
      createMockNode('node_0', 'pdf', 'pdf_output'),
      createMockNode('node_1', 'gene_expression', 'gex_output'),
      createMockNode('node_2', 'gene'),
    ]
    const extractors = getExtractors(nodes)
    expect(extractors).toHaveLength(2)
    expect(extractors[0].data.agent_id).toBe('pdf')
    expect(extractors[1].data.agent_id).toBe('gene_expression')
  })

  it('excludes non-extraction agents', () => {
    const nodes = [
      createMockNode('node_0', 'pdf'),
      createMockNode('node_1', 'gene'),
      createMockNode('node_2', 'allele'),
      createMockNode('node_3', 'disease'),
    ]
    const extractors = getExtractors(nodes)
    expect(extractors).toHaveLength(1)
    expect(extractors[0].data.agent_id).toBe('pdf')
  })
})

// =============================================================================
// validatorHasExplicitExtractorInput Tests
// =============================================================================

describe('validatorHasExplicitExtractorInput', () => {
  it('returns false when input_source is not custom', () => {
    const node = createMockNode('node_0', 'gene')
    node.data.input_source = 'previous_output'
    const extractors = [createMockNode('node_1', 'pdf', 'pdf_output')]

    expect(validatorHasExplicitExtractorInput(node, extractors)).toBe(false)
  })

  it('returns false when custom_input is empty', () => {
    const node = createMockNode('node_0', 'gene')
    node.data.input_source = 'custom'
    node.data.custom_input = ''
    const extractors = [createMockNode('node_1', 'pdf', 'pdf_output')]

    expect(validatorHasExplicitExtractorInput(node, extractors)).toBe(false)
  })

  it('returns false when custom_input has no extractor reference', () => {
    const node = createMockNode('node_0', 'gene')
    node.data.input_source = 'custom'
    node.data.custom_input = 'some static text'
    const extractors = [createMockNode('node_1', 'pdf', 'pdf_output')]

    expect(validatorHasExplicitExtractorInput(node, extractors)).toBe(false)
  })

  it('returns true when custom_input references pdf_output', () => {
    const node = createMockNode('node_0', 'gene')
    node.data.input_source = 'custom'
    node.data.custom_input = '{{pdf_output}}'
    const extractors = [createMockNode('node_1', 'pdf', 'pdf_output')]

    expect(validatorHasExplicitExtractorInput(node, extractors)).toBe(true)
  })

  it('returns true when custom_input references gene_expression_output', () => {
    const node = createMockNode('node_0', 'gene')
    node.data.input_source = 'custom'
    node.data.custom_input = '{{gex_output}}'
    const extractors = [createMockNode('node_1', 'gene_expression', 'gex_output')]

    expect(validatorHasExplicitExtractorInput(node, extractors)).toBe(true)
  })

  it('returns true when custom_input has extractor ref among other text', () => {
    const node = createMockNode('node_0', 'gene')
    node.data.input_source = 'custom'
    node.data.custom_input = 'Process this: {{pdf_output}} and validate'
    const extractors = [createMockNode('node_1', 'pdf', 'pdf_output')]

    expect(validatorHasExplicitExtractorInput(node, extractors)).toBe(true)
  })

  it('returns false when extractor ref is for a deleted extractor', () => {
    const node = createMockNode('node_0', 'gene')
    node.data.input_source = 'custom'
    node.data.custom_input = '{{deleted_output}}'
    const extractors = [createMockNode('node_1', 'pdf', 'pdf_output')]

    expect(validatorHasExplicitExtractorInput(node, extractors)).toBe(false)
  })
})

// =============================================================================
// validatorNeedsConfiguration Tests
// =============================================================================

describe('validatorNeedsConfiguration', () => {
  describe('when validator has explicit extractor input', () => {
    it('returns needsConfig: false', () => {
      const nodes = [
        createMockNode('node_0', 'pdf', 'pdf_output'),
        createMockNode('node_1', 'pdf', 'pdf2_output'),
        (() => {
          const n = createMockNode('node_2', 'gene')
          n.data.input_source = 'custom'
          n.data.custom_input = '{{pdf_output}}'
          return n
        })(),
      ]

      const result = validatorNeedsConfiguration('node_2', nodes, [])
      expect(result.needsConfig).toBe(false)
    })
  })

  describe('when validator is connected to upstream extractor', () => {
    it('returns needsConfig: false regardless of global extractor count', () => {
      const nodes = [
        createMockNode('node_0', 'pdf', 'pdf_output'),
        createMockNode('node_1', 'gene_expression', 'gex_output'),
        createMockNode('node_2', 'gene'),
      ]
      // node_2 is connected to node_0 (pdf)
      const edges = [{ source: 'node_0', target: 'node_2' }]

      const result = validatorNeedsConfiguration('node_2', nodes, edges)
      expect(result.needsConfig).toBe(false)
    })
  })

  describe('when validator is disconnected', () => {
    it('returns needsConfig: false when no extractors exist', () => {
      const nodes = [createMockNode('node_0', 'gene')]

      const result = validatorNeedsConfiguration('node_0', nodes, [])
      expect(result.needsConfig).toBe(false)
    })

    it('returns needsConfig: false when only one extractor exists', () => {
      const nodes = [
        createMockNode('node_0', 'pdf', 'pdf_output'),
        createMockNode('node_1', 'gene'),
      ]

      const result = validatorNeedsConfiguration('node_1', nodes, [])
      expect(result.needsConfig).toBe(false)
    })

    it('returns needsConfig: true when multiple extractors exist', () => {
      const nodes = [
        createMockNode('node_0', 'pdf', 'pdf_output'),
        createMockNode('node_1', 'gene_expression', 'gex_output'),
        createMockNode('node_2', 'gene'),
      ]

      const result = validatorNeedsConfiguration('node_2', nodes, [])
      expect(result.needsConfig).toBe(true)
      expect(result.reason).toContain('Multiple extractors')
    })
  })

  describe('edge cases', () => {
    it('returns needsConfig: false for non-validator nodes', () => {
      const nodes = [createMockNode('node_0', 'pdf', 'pdf_output')]

      const result = validatorNeedsConfiguration('node_0', nodes, [])
      expect(result.needsConfig).toBe(false)
    })

    it('returns needsConfig: false for non-existent node', () => {
      const nodes = [createMockNode('node_0', 'gene')]

      const result = validatorNeedsConfiguration('nonexistent', nodes, [])
      expect(result.needsConfig).toBe(false)
    })

    it('handles multi-hop connections to extractors', () => {
      // pdf -> allele -> gene
      const nodes = [
        createMockNode('node_0', 'pdf', 'pdf_output'),
        createMockNode('node_1', 'gene_expression', 'gex_output'), // Another extractor, disconnected
        createMockNode('node_2', 'allele'),
        createMockNode('node_3', 'gene'),
      ]
      const edges = [
        { source: 'node_0', target: 'node_2' },
        { source: 'node_2', target: 'node_3' },
      ]

      const result = validatorNeedsConfiguration('node_3', nodes, edges)
      // node_3 is connected to pdf through allele, so no error
      expect(result.needsConfig).toBe(false)
    })
  })
})
