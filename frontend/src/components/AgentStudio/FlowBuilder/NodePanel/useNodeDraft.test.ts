import { act, renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { AgentExecutionReceipt } from '@/types/agentExecution'
import type { AgentNode } from '../types'
import { useNodeDraft } from './useNodeDraft'

const receipt: AgentExecutionReceipt = {
  agent_id: 'agent-1', agent_key: 'ca_agent-1', agent_revision_id: 'revision-1',
  revision: 1, fingerprint: 'fingerprint', output_contract: { output_state: 'none' },
}

function node(): AgentNode {
  return {
    id: 'node-1', type: 'agent', position: { x: 0, y: 0 },
    data: { agent_id: receipt.agent_key, agent_display_name: 'Custom',
      agent_revision_id: receipt.agent_revision_id, output_key: 'result',
      custom_instructions: 'Saved instructions' },
  }
}

function setup() {
  const original = node()
  const hook = renderHook(({ source }) => useNodeDraft({
    node: source, agentMetadata: {}, isTaskInput: false, supportsFileOutputNaming: false,
  }), { initialProps: { source: original } })
  const acknowledged = { ...original, data: { ...original.data, execution_receipt: receipt } }
  return { ...hook, original, acknowledged }
}

describe('node draft save acknowledgements', () => {
  it('hydrates the same revision receipt without discarding unapplied instructions', () => {
    const { result, rerender, acknowledged } = setup()
    act(() => result.current.set('customInstructions', 'Unapplied correction'))
    rerender({ source: acknowledged })
    expect(result.current.values.customInstructions).toBe('Unapplied correction')
    expect(result.current.buildPayload()).toMatchObject({ execution_receipt: receipt })
    expect(result.current.dirty).toBe(true)
    act(() => result.current.reset())
    expect(result.current.values.customInstructions).toBe('Saved instructions')
    expect(result.current.dirty).toBe(false)
  })

  it('preserves an unapplied retarget when the old revision save is acknowledged', () => {
    const { result, rerender, acknowledged } = setup()
    const newer = { ...receipt, agent_revision_id: 'revision-2', revision: 2 }
    act(() => result.current.set('executionSelection', {
      agent_revision_id: newer.agent_revision_id, execution_receipt: newer,
    }))
    rerender({ source: acknowledged })
    expect(result.current.buildPayload()).toMatchObject({ execution_receipt: newer })
    expect(result.current.dirty).toBe(true)
  })

  it('does not mark receipt hydration as a curator edit', () => {
    const { result, rerender, acknowledged } = setup()
    rerender({ source: acknowledged })
    expect(result.current.values.executionSelection.execution_receipt).toEqual(receipt)
    expect(result.current.dirty).toBe(false)
  })

  it.each(['different node', 'changed settings'])('still resets for %s', (change) => {
    const { result, rerender, acknowledged } = setup()
    act(() => result.current.set('customInstructions', 'Unapplied correction'))
    rerender({ source: change === 'different node'
      ? { ...acknowledged, id: 'node-2' }
      : { ...acknowledged, data: { ...acknowledged.data, custom_instructions: 'Applied update' } } })
    expect(result.current.values.customInstructions).toBe(
      change === 'different node' ? 'Saved instructions' : 'Applied update',
    )
    expect(result.current.dirty).toBe(false)
  })
})
