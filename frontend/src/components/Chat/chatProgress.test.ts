import { describe, expect, it } from 'vitest'

import type { SSEEvent } from '@/hooks/useChatStream'

import {
  getFriendlyProgressMessage,
  isNonRenderedMetadataEvent,
  shouldShowInChat,
} from './chatProgress'

describe('chatProgress', () => {
  it('limits chat-visible progress to supported event types', () => {
    expect(shouldShowInChat('SUPERVISOR_START')).toBe(true)
    expect(shouldShowInChat('TOOL_COMPLETE')).toBe(true)
    expect(shouldShowInChat('PENDING_USER_INPUT')).toBe(true)
    expect(shouldShowInChat('CHUNK_PROVENANCE')).toBe(false)
    expect(shouldShowInChat('RUN_STARTED')).toBe(false)
  })

  it('identifies stream metadata events that are not chat-rendered', () => {
    expect(isNonRenderedMetadataEvent('CHUNK_PROVENANCE')).toBe(true)
    expect(isNonRenderedMetadataEvent('TEXT_MESSAGE_CONTENT')).toBe(false)
  })

  it('uses friendly tool labels without duplicating completion text', () => {
    const searchCompleteEvent: SSEEvent = {
      type: 'TOOL_COMPLETE',
      details: { friendlyName: 'Search PubMed' },
    }
    const alreadyCompleteEvent: SSEEvent = {
      type: 'TOOL_COMPLETE',
      details: { friendlyName: 'Search PubMed complete' },
    }

    expect(getFriendlyProgressMessage(searchCompleteEvent)).toBe('Search PubMed complete')
    expect(getFriendlyProgressMessage(alreadyCompleteEvent)).toBe('Search PubMed complete')
  })

  it('uses action-required copy for refinement events', () => {
    expect(getFriendlyProgressMessage({ type: 'PENDING_USER_INPUT' })).toBe(
      'Action required: please refine the query (limit/filter).',
    )
    expect(getFriendlyProgressMessage({ type: 'DOMAIN_SKIPPED' })).toBe(
      'Action required: please refine the query (limit/filter).',
    )
  })
})

describe('supervisor review progress', () => {
  it('shows phase-level progress without exposing token-level events', () => {
    expect(shouldShowInChat('AGENT_GENERATING')).toBe(false)
    expect(shouldShowInChat('AGENT_GENERATING', { reviewPhase: 'supervisor_review' })).toBe(true)
    expect(getFriendlyProgressMessage({ type: 'AGENT_GENERATING', details: {
      message: 'Reviewing extracted findings and validation results',
    } })).toBe('Reviewing extracted findings and validation results')
  })

  it.each([
    { reviewPhase: 'initial_validation' }, { severity: 'warning' }, { error: 'failure' },
    { success: false }, { hasError: true },
  ])('does not turn an explicit phase outcome into a success label: %j', (details) => {
    expect(getFriendlyProgressMessage({ type: 'TOOL_COMPLETE', details: {
      ...details, friendlyName: 'Initial validation stopped',
    } })).toBe('Initial validation stopped')
  })
})

it('does not describe terminal review events as active when message is absent', () => {
  expect(getFriendlyProgressMessage({ type: 'AGENT_COMPLETE' })).toBe('Supervisor review ended')
  expect(getFriendlyProgressMessage({ type: 'SUPERVISOR_ERROR' })).toBe('Supervisor review failed')
})
