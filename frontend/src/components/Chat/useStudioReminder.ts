import { useCallback, useEffect, useRef, useState } from 'react'

import { safeGetItem, safeSetItem } from '@/lib/browserStorage'
import type { Message } from './types'

interface ReminderConfig {
  enabled: boolean
  message_chars: number
  context_chars: number
  context_messages: number
}

// Browser-scoped persistence contains no conversation content or provider keys.
const storageContext = { owner: 'chat' as const, quiet: true }

export function useStudioReminder(userId: string | null, sessionId: string | null | undefined) {
  const key = userId && sessionId ? `studio-reminder:${userId}:${sessionId}` : null
  const currentKey = useRef(key)
  currentKey.current = key
  const pending = useRef<AbortController | null>(null)
  const seen = useRef(new Set<string>())
  const config = useRef<ReminderConfig | null>(null)
  const [visibleKey, setVisibleKey] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    config.current = null
    if (userId) {
      void fetch('/api/chat/studio-reminder/config', { credentials: 'include', signal: controller.signal })
        .then(response => response.ok ? response.json() : null)
        .then(value => { if (!controller.signal.aborted) config.current = value })
        .catch(() => { /* An optional reminder never interrupts chat. */ })
    }
    return () => { controller.abort(); config.current = null }
  }, [userId])

  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key === key && event.newValue) {
        if (key) seen.current.add(key)
        pending.current?.abort()
        setVisibleKey(null)
      }
    }
    window.addEventListener('storage', onStorage)
    return () => {
      pending.current?.abort()
      pending.current = null
      window.removeEventListener('storage', onStorage)
    }
  }, [key])

  const screen = useCallback((message: string, messages: Message[]) => {
    const settings = config.current
    if (!key || !sessionId || !settings?.enabled || pending.current || seen.current.has(key)) return
    const stored = safeGetItem(() => window.localStorage, key, storageContext)
    if (stored.ok && stored.value) return
    const controller = new AbortController()
    pending.current = controller
    const history = messages.filter(item => item.role === 'user' || item.role === 'assistant')
    const recent = settings.context_messages > 0 ? history.slice(-settings.context_messages) : []
    void fetch(`/api/chat/session/${encodeURIComponent(sessionId)}/studio-reminder`, {
      method: 'POST', credentials: 'include', signal: controller.signal,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: message.slice(0, settings.message_chars),
        recent_context: recent.map(item => ({ role: item.role, text: item.content.slice(0, settings.context_chars) })),
      }),
    })
      .then(response => response.ok ? response.json() : null)
      .then(result => {
        if (controller.signal.aborted || currentKey.current !== key || !result?.show_reminder) return
        seen.current.add(key)
        safeSetItem(() => window.localStorage, key, 'shown', storageContext)
        setVisibleKey(key)
      })
      .catch(() => { /* Fail open: leave normal chat running. */ })
      .finally(() => { if (pending.current === controller) pending.current = null })
  }, [key, sessionId])

  const dismiss = useCallback(() => {
    if (key) {
      seen.current.add(key)
      safeSetItem(() => window.localStorage, key, 'dismissed', storageContext)
    }
    pending.current?.abort()
    setVisibleKey(null)
  }, [key])

  return { visible: Boolean(key && visibleKey === key), screen, dismiss }
}
