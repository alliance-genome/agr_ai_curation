import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  notifyFlowListInvalidated,
  subscribeToFlowListInvalidation,
} from './flowListInvalidation'
import logger from '@/services/logger'

vi.mock('@/services/logger', () => ({
  default: {
    warn: vi.fn(),
  },
}))

describe('flowListInvalidation', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('logs storage write failures without swallowing the active-tab invalidation event', () => {
    const onInvalidate = vi.fn()
    const unsubscribe = subscribeToFlowListInvalidation(onInvalidate)
    const setItemSpy = vi
      .spyOn(Storage.prototype, 'setItem')
      .mockImplementation(() => {
        throw new Error('Quota exceeded')
      })

    notifyFlowListInvalidated({ flowId: 'flow-1', reason: 'created' })

    expect(onInvalidate).toHaveBeenCalledTimes(1)
    expect(logger.warn).toHaveBeenCalledWith(
      'Failed to persist flow list invalidation event',
      expect.objectContaining({
        component: 'flowListInvalidation',
        metadata: expect.objectContaining({
          error: 'Quota exceeded',
          flowId: 'flow-1',
          reason: 'created',
        }),
      })
    )

    unsubscribe()
    setItemSpy.mockRestore()
  })
})
