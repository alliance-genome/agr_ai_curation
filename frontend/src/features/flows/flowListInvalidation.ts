import logger from '@/services/logger'

export type FlowListInvalidationReason = 'created' | 'updated' | 'deleted'

export interface FlowListInvalidationDetail {
  flowId?: string
  reason: FlowListInvalidationReason
  occurredAt: string
}

const FLOW_LIST_INVALIDATED_EVENT = 'agr-flow-list-invalidated'
const FLOW_LIST_INVALIDATED_STORAGE_KEY = 'agr-flow-list-invalidated'

export function notifyFlowListInvalidated(
  detail: Omit<FlowListInvalidationDetail, 'occurredAt'>
): void {
  if (typeof window === 'undefined') {
    return
  }

  const eventDetail: FlowListInvalidationDetail = {
    ...detail,
    occurredAt: new Date().toISOString(),
  }

  window.dispatchEvent(
    new CustomEvent<FlowListInvalidationDetail>(FLOW_LIST_INVALIDATED_EVENT, {
      detail: eventDetail,
    })
  )

  try {
    window.localStorage.setItem(
      FLOW_LIST_INVALIDATED_STORAGE_KEY,
      JSON.stringify(eventDetail)
    )
  } catch (error) {
    logger.warn('Failed to persist flow list invalidation event', {
      component: 'flowListInvalidation',
      metadata: {
        error: error instanceof Error ? error.message : 'Unknown error',
        flowId: eventDetail.flowId,
        reason: eventDetail.reason,
      },
    })
  }
}

export function subscribeToFlowListInvalidation(onInvalidate: () => void): () => void {
  if (typeof window === 'undefined') {
    return () => {}
  }

  const handleInvalidate = () => {
    onInvalidate()
  }

  const handleStorage = (event: StorageEvent) => {
    if (event.key !== FLOW_LIST_INVALIDATED_STORAGE_KEY || !event.newValue) {
      return
    }

    onInvalidate()
  }

  window.addEventListener(FLOW_LIST_INVALIDATED_EVENT, handleInvalidate as EventListener)
  window.addEventListener('storage', handleStorage)

  return () => {
    window.removeEventListener(FLOW_LIST_INVALIDATED_EVENT, handleInvalidate as EventListener)
    window.removeEventListener('storage', handleStorage)
  }
}
