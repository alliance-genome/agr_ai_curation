import { describe, expect, it } from 'vitest'
import { importedChatContext } from './importedChatContext'

describe('imported main-chat context', () => {
  it('restores source chat and trace independently of the Studio trace', () => {
    expect(importedChatContext([
      { payload_json: { debug_context: { source_session_id: 'main-chat' }, trace_capture: { status: 'provided_context_trace_id', trace_id: 'main-trace' } } },
      { payload_json: { trace_capture: { status: 'captured', trace_id: 'studio-trace', source_trace_id: 'main-trace' } } },
    ])).toEqual({ sourceSessionId: 'main-chat', traceId: 'main-trace' })
  })
  it('does not turn a regular Studio trace into imported main-chat context', () => {
    expect(importedChatContext([{ payload_json: { trace_capture: { status: 'captured', trace_id: 'studio-trace' } } }]))
      .toEqual({ sourceSessionId: undefined, traceId: undefined })
  })
  it('ignores malformed payloads', () => {
    expect(importedChatContext([{ payload_json: null }, { payload_json: [] }, { payload_json: { debug_context: { source_session_id: {} } } }]))
      .toEqual({ sourceSessionId: undefined, traceId: undefined })
  })
})
