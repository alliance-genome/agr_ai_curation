import { describe, expect, it } from 'vitest'

import type { ToolIdeaRequest } from '@/types/promptExplorer'
import {
  changedOverrideGroups,
  computeDirtyState,
  describeChangedSections,
  formatCharCount,
  formatRelativeTime,
  resolveReasoningSelection,
  shortRequestId,
  toolIdeaStatusLabel,
  type DraftFields,
} from './workshopDraftUtils'

function buildFields(overrides: Partial<DraftFields> = {}): DraftFields {
  return {
    name: 'Agent',
    description: '',
    customPrompt: 'Prompt',
    groupPromptOverrides: {},
    includeGroupRules: true,
    visibility: 'private',
    allowedGroupIds: [],
    modelId: 'model-a',
    modelReasoning: 'medium',
    toolIds: ['search_document'],
    outputSchemaKey: '',
    icon: 'x',
    ...overrides,
  }
}

function buildRequest(overrides: Partial<ToolIdeaRequest> = {}): ToolIdeaRequest {
  return {
    id: 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
    user_id: 1,
    title: 'Need a tool',
    description: 'desc',
    opus_conversation: [],
    status: 'submitted',
    created_at: '2026-08-28T10:00:00Z',
    updated_at: '2026-08-28T10:00:00Z',
    ...overrides,
  }
}

describe('computeDirtyState', () => {
  it('reports clean when the draft matches the snapshot', () => {
    const fields = buildFields()
    const dirty = computeDirtyState(fields, { ...fields, toolIds: [...fields.toolIds] })
    expect(dirty).toEqual({ setup: false, prompt: false, tools: false, groups: [], any: false })
  })

  it('reports clean when there is no snapshot yet', () => {
    expect(computeDirtyState(buildFields(), null).any).toBe(false)
  })

  it('attributes identity, model, and sharing edits to Setup', () => {
    const saved = buildFields()
    expect(computeDirtyState(buildFields({ name: 'Renamed' }), saved).setup).toBe(true)
    expect(computeDirtyState(buildFields({ modelReasoning: 'high' }), saved).setup).toBe(true)
    expect(computeDirtyState(buildFields({ visibility: 'project' }), saved).setup).toBe(true)
    expect(computeDirtyState(buildFields({ allowedGroupIds: ['ZFIN'] }), saved).setup).toBe(true)
    expect(computeDirtyState(buildFields({ name: 'Renamed' }), saved).prompt).toBe(false)
  })

  it('attributes prompt text and the runtime toggle to Prompt', () => {
    const saved = buildFields()
    const dirty = computeDirtyState(buildFields({ customPrompt: 'Changed', includeGroupRules: false }), saved)
    expect(dirty.prompt).toBe(true)
    expect(dirty.setup).toBe(false)
    expect(dirty.any).toBe(true)
  })

  it('lists each group whose override changed', () => {
    const saved = buildFields({ groupPromptOverrides: { ZFIN: 'old', MGI: 'same' } })
    const dirty = computeDirtyState(
      buildFields({ groupPromptOverrides: { ZFIN: 'new', MGI: 'same', FB: 'added' } }),
      saved
    )
    expect(dirty.groups).toEqual(['FB', 'ZFIN'])
    expect(dirty.prompt).toBe(false)
  })

  it('ignores tool order but not tool membership', () => {
    const saved = buildFields({ toolIds: ['a', 'b'] })
    expect(computeDirtyState(buildFields({ toolIds: ['b', 'a'] }), saved).tools).toBe(false)
    expect(computeDirtyState(buildFields({ toolIds: ['a'] }), saved).tools).toBe(true)
  })
})

describe('describeChangedSections', () => {
  it('produces the Save dialog line in section order', () => {
    const saved = buildFields({ groupPromptOverrides: {} })
    const dirty = computeDirtyState(
      buildFields({ name: 'X', customPrompt: 'Y', groupPromptOverrides: { ZFIN: 'z' }, toolIds: [] }),
      saved
    )
    expect(describeChangedSections(dirty)).toEqual(['Setup', 'Your prompt', 'ZFIN instructions', 'Tools'])
  })

  it('returns an empty list for a clean draft', () => {
    const fields = buildFields()
    expect(describeChangedSections(computeDirtyState(fields, fields))).toEqual([])
  })
})

describe('changedOverrideGroups', () => {
  it('detects removed overrides', () => {
    expect(changedOverrideGroups({}, { WB: 'text' })).toEqual(['WB'])
  })
})

describe('formatting helpers', () => {
  it('formats character counts compactly', () => {
    expect(formatCharCount('')).toBe('0')
    expect(formatCharCount('a'.repeat(999))).toBe('999')
    expect(formatCharCount('a'.repeat(1800))).toBe('1.8k')
    expect(formatCharCount('a'.repeat(12400))).toBe('12k')
  })

  it('formats relative save times', () => {
    const now = 1_000_000_000
    expect(formatRelativeTime(now - 10_000, now)).toBe('just now')
    expect(formatRelativeTime(now - 120_000, now)).toBe('2 min ago')
    expect(formatRelativeTime(now - 3 * 3_600_000, now)).toBe('3 h ago')
    expect(formatRelativeTime(now - 2 * 86_400_000, now)).toBe('2 d ago')
  })

  it('shortens request ids for display', () => {
    expect(shortRequestId('aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee')).toBe('aaaaaa')
  })

  it('maps tool request statuses to curator labels', () => {
    expect(toolIdeaStatusLabel(buildRequest({ status: 'submitted' }))).toBe('New')
    expect(toolIdeaStatusLabel(buildRequest({ status: 'in_progress' }))).toBe('In progress')
    expect(toolIdeaStatusLabel(buildRequest({ status: 'completed' }))).toBe('Shipped')
    expect(
      toolIdeaStatusLabel(buildRequest({ status: 'completed', resulting_tool_key: 'go_lookup' }))
    ).toBe('Shipped go_lookup')
    expect(toolIdeaStatusLabel(buildRequest({ status: 'declined' }))).toBe('Declined')
  })
})

describe('resolveReasoningSelection', () => {
  const models = [
    {
      model_id: 'm',
      name: 'M',
      provider: 'p',
      description: '',
      guidance: '',
      default: true,
      supports_reasoning: true,
      supports_temperature: false,
      reasoning_options: ['low', 'medium', 'high'],
      default_reasoning: 'medium',
      reasoning_descriptions: {},
      recommended_for: [],
      avoid_for: [],
    },
  ]

  it('keeps a valid candidate, falls back to the model default, then the first option', () => {
    expect(resolveReasoningSelection(models, 'm', 'HIGH')).toBe('high')
    expect(resolveReasoningSelection(models, 'm', 'bogus')).toBe('medium')
    expect(resolveReasoningSelection([{ ...models[0], default_reasoning: undefined }], 'm')).toBe('low')
    expect(resolveReasoningSelection(models, 'missing')).toBe('')
  })
})
