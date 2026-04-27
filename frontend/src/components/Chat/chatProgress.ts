import type { SSEEvent } from '@/hooks/useChatStream'

const NON_RENDERED_METADATA_EVENTS = [
  'CHUNK_PROVENANCE',
] as const

/**
 * Filter function to determine which audit events should show in chat progress.
 * Chat shows: SUPERVISOR_START, CREW_START, TOOL_START, TOOL_COMPLETE,
 * LLM_CALL, SUPERVISOR_COMPLETE, DOMAIN_SKIPPED, DOMAIN_WARNING,
 * PENDING_USER_INPUT, and STOP_CONFIRMED.
 */
export function shouldShowInChat(eventType: string): boolean {
  const chatEvents = [
    'SUPERVISOR_START',
    'CREW_START',
    'TOOL_START',
    'TOOL_COMPLETE',
    'LLM_CALL',
    'SUPERVISOR_COMPLETE',
    'DOMAIN_SKIPPED',
    'DOMAIN_WARNING',
    'PENDING_USER_INPUT',
    'STOP_CONFIRMED',
  ]
  return chatEvents.includes(eventType)
}

export function isNonRenderedMetadataEvent(eventType: string): boolean {
  return NON_RENDERED_METADATA_EVENTS.includes(
    eventType as (typeof NON_RENDERED_METADATA_EVENTS)[number],
  )
}

/**
 * Convert audit event to friendly progress message.
 * Extracts friendlyName from event details for user-friendly display.
 */
export function getFriendlyProgressMessage(event: SSEEvent): string {
  switch (event.type) {
    case 'SUPERVISOR_START':
      return event.details?.message || 'Starting...'
    case 'STOP_CONFIRMED':
      return 'Interaction stopped by user'

    case 'CREW_START':
      return event.details?.crewDisplayName || `Starting ${event.details?.crewName || 'crew'}...`

    case 'TOOL_START':
      return event.details?.friendlyName || `Using ${event.details?.toolName || 'tool'}...`

    case 'TOOL_COMPLETE':
      if (event.details?.friendlyName) {
        const name = event.details.friendlyName
        return name.toLowerCase().endsWith('complete') ? name : `${name} complete`
      }
      return 'Tool complete'

    case 'LLM_CALL':
      return event.details?.message || 'Thinking...'

    case 'DOMAIN_WARNING':
      return event.details?.message || 'Warning received.'

    case 'PENDING_USER_INPUT':
      return event.details?.message || 'Action required: please refine the query (limit/filter).'

    case 'DOMAIN_SKIPPED':
      return event.details?.message || 'Action required: please refine the query (limit/filter).'

    case 'SUPERVISOR_COMPLETE':
      return event.details?.message || 'Complete'

    default:
      return 'Processing...'
  }
}
