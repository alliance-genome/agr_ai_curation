import { LatestIntent, type LatestIntentOperation } from '@/lib/latestIntent'

const latestChatDocumentIntent = new LatestIntent()

/** Starts an operation in the shared latest-wins chat document scope. */
export const beginChatDocumentIntent = (): LatestIntentOperation => (
  latestChatDocumentIntent.begin()
)

/** Invalidates an operation only while it still owns the shared document scope. */
export const invalidateChatDocumentIntent = (operation: LatestIntentOperation | null): void => {
  if (operation?.ownsLatest()) {
    latestChatDocumentIntent.invalidate()
  }
}
