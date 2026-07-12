import { describe, expect, it } from 'vitest'

import {
  beginChatDocumentIntent,
  invalidateChatDocumentIntent,
} from './chatDocumentIntent'

describe('chatDocumentIntent', () => {
  it('shares ownership across callers and ignores stale cleanup', () => {
    const routeOperation = beginChatDocumentIntent()
    const uploadOperation = beginChatDocumentIntent()

    expect(routeOperation.signal.aborted).toBe(true)
    expect(routeOperation.ownsLatest()).toBe(false)
    expect(uploadOperation.ownsLatest()).toBe(true)

    invalidateChatDocumentIntent(routeOperation)
    expect(uploadOperation.signal.aborted).toBe(false)
    expect(uploadOperation.ownsLatest()).toBe(true)

    invalidateChatDocumentIntent(uploadOperation)
    expect(uploadOperation.signal.aborted).toBe(true)
    expect(uploadOperation.ownsLatest()).toBe(false)
  })
})
