import type { Browser, BrowserContext, Page } from 'playwright'
import type { PlaywrightAgent } from '@midscene/web/playwright'

import type { ApiClient, ApiEvidence } from './api-client.js'
import type { SmokeConfig } from './config.js'
import type { CleanupResult, TrackedResources } from './resources.js'
import type { SseEvent } from './sse.js'

export interface StreamCapture {
  path: '/api/chat/stream' | '/api/chat/execute-flow'
  request: Record<string, unknown>
  status: number
  events: SseEvent[]
}

export interface CaseState {
  name: string
  startedAt: string
  resources: TrackedResources
  apiEvidence: ApiEvidence[]
  browserEvidence: ApiEvidence[]
  streamCaptures: StreamCapture[]
  preparedFile?: string
  documentId?: string
  flowId?: string
  flowExecutionCount?: number
  chatSessionId?: string
  cleanup?: CleanupResult
}

export interface SmokeContext {
  config: SmokeConfig
  browser: Browser
  browserContext: BrowserContext
  page: Page
  api: ApiClient
  agents: Map<string, PlaywrightAgent>
  pendingCaptures: Set<Promise<void>>
  caseState?: CaseState
}
