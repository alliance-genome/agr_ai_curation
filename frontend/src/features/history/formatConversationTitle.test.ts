import { describe, expect, it } from 'vitest'

import formatConversationTitle, {
  UNTITLED_CONVERSATION_LABEL,
  hasConversationTitle,
} from './formatConversationTitle'

describe('formatConversationTitle', () => {
  it('returns the trimmed stored title', () => {
    expect(formatConversationTitle({ session_id: 'abc', title: '  TP53 review  ' })).toBe('TP53 review')
    expect(hasConversationTitle({ session_id: 'abc', title: '  TP53 review  ' })).toBe(true)
  })

  it('labels missing or blank titles as untitled', () => {
    expect(formatConversationTitle({ session_id: 'abcdef123', title: null })).toBe(UNTITLED_CONVERSATION_LABEL)
    expect(formatConversationTitle({ session_id: 'abcdef123', title: '   ' })).toBe(UNTITLED_CONVERSATION_LABEL)
    expect(hasConversationTitle({ session_id: 'abcdef123', title: '   ' })).toBe(false)
  })

  it('returns an empty string for a missing session', () => {
    expect(formatConversationTitle(null)).toBe('')
    expect(hasConversationTitle(null)).toBe(false)
  })
})
