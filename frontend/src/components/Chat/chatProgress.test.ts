import { describe, expect, it } from 'vitest'

import type { SSEEvent } from '@/hooks/useChatStream'

import {
  getFriendlyProgressMessage,
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

  it('falls back to action-required copy for refinement events', () => {
    expect(getFriendlyProgressMessage({ type: 'PENDING_USER_INPUT' })).toBe(
      'Action required: please refine the query (limit/filter).',
    )
    expect(getFriendlyProgressMessage({ type: 'DOMAIN_SKIPPED' })).toBe(
      'Action required: please refine the query (limit/filter).',
    )
  })
})
