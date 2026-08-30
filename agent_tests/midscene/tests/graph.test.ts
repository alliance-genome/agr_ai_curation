import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { assertExactSmokeGraph, buildSmokeFlowDefinition } from '../src/graph.js'

describe('flow graph assertions', () => {
  it('accepts the exact control and output-attachment graph', () => {
    const graph = buildSmokeFlowDefinition({ instructions: 'Do the smoke task.', formatter: 'json_formatter' })
    assert.doesNotThrow(() => assertExactSmokeGraph(graph, {
      taskInstructions: 'Do the smoke task.', formatter: 'json_formatter',
    }))
  })

  it('normalizes a missing control edge role', () => {
    const graph = buildSmokeFlowDefinition({ instructions: 'Do it.', formatter: 'csv_formatter' })
    const firstEdge = (graph.edges as Array<Record<string, unknown>>)[0]
    delete firstEdge?.role
    assert.doesNotThrow(() => assertExactSmokeGraph(graph, {
      taskInstructions: 'Do it.', formatter: 'csv_formatter',
    }))
  })

  it('rejects stale nodes and stale edges', () => {
    const graph = buildSmokeFlowDefinition({ instructions: 'Updated.', formatter: 'csv_formatter' })
    ;(graph.nodes as unknown[]).push({ id: 'stale-json', type: 'agent', data: { agent_id: 'json_formatter' } })
    assert.throws(() => assertExactSmokeGraph(graph, {
      taskInstructions: 'Updated.', formatter: 'csv_formatter',
    }), /exactly 3 nodes/)
  })

  it('rejects an output formatter connected as control flow', () => {
    const graph = buildSmokeFlowDefinition({ instructions: 'Do it.', formatter: 'json_formatter' })
    ;(graph.edges as Array<Record<string, unknown>>)[1]!.role = 'control_flow'
    assert.throws(() => assertExactSmokeGraph(graph, {
      taskInstructions: 'Do it.', formatter: 'json_formatter',
    }), /missing graph edge/)
  })
})
