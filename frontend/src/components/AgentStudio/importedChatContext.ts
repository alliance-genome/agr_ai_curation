/** Recover source-chat pointers from durable Studio turns, without treating the
 * Studio response's own trace as the original chat trace. Reads of these IDs
 * are still authorized by the backend chat and trace tools. */
export function importedChatContext(messages: Array<{ payload_json?: unknown }>): {
  sourceSessionId?: string
  traceId?: string
} {
  let sourceSessionId: string | undefined
  let traceId: string | undefined
  const text = (value: unknown) => typeof value === 'string' && value.trim() ? value.trim() : undefined
  const object = (value: unknown): Record<string, unknown> => value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : {}
  for (const message of messages) {
    const payload = object(message.payload_json)
    sourceSessionId ||= text(object(payload.debug_context).source_session_id)
    const capture = object(payload.trace_capture)
    traceId ||= text(capture.source_trace_id)
    if (capture.status === 'provided_context_trace_id') traceId ||= text(capture.trace_id)
  }
  return { sourceSessionId, traceId }
}
