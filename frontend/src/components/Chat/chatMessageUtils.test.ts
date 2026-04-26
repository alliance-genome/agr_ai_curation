import { beforeEach, describe, expect, it } from 'vitest'

import { getChatLocalStorageKeys } from '@/lib/chatCacheKeys'
import type { EvidenceRecord } from '@/features/curation/types'

import {
  loadMessagesFromStorage,
  sanitizeStoredMessage,
  withMissingEvidenceReviewAndCurateTargets,
} from './chatMessageUtils'
import type { Message, SerializedMessage, StoredChatData } from './types'

const evidenceRecord: EvidenceRecord = {
  entity: 'unc-26',
  verified_quote: 'UNC-26 is required for normal synaptic transmission.',
  page: 7,
  section: 'Results',
  chunk_id: 'chunk-1',
}

describe('chatMessageUtils', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('sanitizes unavailable stored messages with a Date timestamp', () => {
    const storedMessage: SerializedMessage = {
      role: 'assistant',
      content: '   ',
      timestamp: '2026-02-03T04:05:06.000Z',
    }

    const restoredMessage = sanitizeStoredMessage(storedMessage)

    expect(restoredMessage.content).toBe('[Message content unavailable]')
    expect(restoredMessage.terminalMessage).toBeNull()
    expect(restoredMessage.timestamp).toBeInstanceOf(Date)
    expect(restoredMessage.timestamp.toISOString()).toBe('2026-02-03T04:05:06.000Z')
  })

  it('restores session-scoped messages only when the stored session matches', () => {
    const storageKeys = getChatLocalStorageKeys('user-1')
    const storedData: StoredChatData = {
      session_id: 'session-1',
      messages: [
        {
          role: 'user',
          content: 'What evidence supports this annotation?',
          timestamp: '2026-02-03T04:05:06.000Z',
          id: 'message-1',
        },
      ],
    }
    localStorage.setItem(storageKeys.messages, JSON.stringify(storedData))

    const restoredMessages = loadMessagesFromStorage(storageKeys, 'session-1')
    const mismatchedMessages = loadMessagesFromStorage(storageKeys, 'session-2')

    expect(restoredMessages).toHaveLength(1)
    expect(restoredMessages[0]).toMatchObject({
      id: 'message-1',
      role: 'user',
      content: 'What evidence supports this annotation?',
    })
    expect(restoredMessages[0].timestamp).toBeInstanceOf(Date)
    expect(mismatchedMessages).toEqual([])
  })

  it('backfills missing Review & Curate targets for supported evidence messages', () => {
    const messages: Message[] = [
      {
        role: 'assistant',
        content: 'Found evidence.',
        timestamp: new Date('2026-02-03T04:05:06.000Z'),
        id: 'assistant-1',
        evidenceRecords: [evidenceRecord],
        evidenceCurationSupported: true,
        evidenceCurationAdapterKey: 'wormbase_gene',
      },
    ]

    const nextMessages = withMissingEvidenceReviewAndCurateTargets(
      messages,
      'document-1',
      'session-1',
    )

    expect(nextMessages).not.toBe(messages)
    expect(nextMessages[0].reviewAndCurateTarget).toEqual({
      documentId: 'document-1',
      originSessionId: 'session-1',
      adapterKeys: ['wormbase_gene'],
    })
  })

  it('leaves unsupported evidence messages without a Review & Curate target', () => {
    const messages: Message[] = [
      {
        role: 'assistant',
        content: 'Found evidence.',
        timestamp: new Date('2026-02-03T04:05:06.000Z'),
        id: 'assistant-1',
        evidenceRecords: [evidenceRecord],
        evidenceCurationSupported: false,
        evidenceCurationAdapterKey: 'wormbase_gene',
      },
    ]

    const nextMessages = withMissingEvidenceReviewAndCurateTargets(
      messages,
      'document-1',
      'session-1',
    )

    expect(nextMessages).toBe(messages)
    expect(nextMessages[0].reviewAndCurateTarget).toBeUndefined()
  })
})
