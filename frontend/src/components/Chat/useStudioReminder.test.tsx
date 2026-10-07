import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useStudioReminder } from './useStudioReminder'

const settings = { enabled: true, message_chars: 1800, context_chars: 650, context_messages: 2 }
const response = (data: unknown) => ({ ok: true, json: async () => data })

describe('Agent Studio reminder', () => {
  beforeEach(() => { localStorage.clear() })
  afterEach(() => { vi.unstubAllGlobals() })

  it('shows once and skips further checks after dismissal and reload', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(response(settings)).mockResolvedValue(response({ show_reminder: true }))
    vi.stubGlobal('fetch', fetch)
    const { result, unmount } = renderHook(() => useStudioReminder('curator', 'chat-a'))
    await act(async () => {})
    act(() => result.current.screen('change the flow', []))
    await waitFor(() => expect(result.current.visible).toBe(true))
    act(() => result.current.dismiss())
    act(() => result.current.screen('change it again', []))
    expect(fetch).toHaveBeenCalledTimes(2)
    unmount()
    fetch.mockResolvedValue(response(settings))
    const resumed = renderHook(() => useStudioReminder('curator', 'chat-a'))
    await act(async () => {})
    act(() => resumed.result.current.screen('change it again', []))
    expect(fetch).toHaveBeenCalledTimes(3)
    expect(resumed.result.current.visible).toBe(false)
  })

  it('does not show a late result in a different chat', async () => {
    let resolve!: (value: unknown) => void
    const fetch = vi.fn().mockResolvedValueOnce(response(settings)).mockImplementation(() => new Promise(r => { resolve = r }))
    vi.stubGlobal('fetch', fetch)
    const { result, rerender } = renderHook(({ session }) => useStudioReminder('curator', session), { initialProps: { session: 'a' } })
    await act(async () => {})
    act(() => result.current.screen('change the prompt', []))
    rerender({ session: 'b' })
    await act(async () => { resolve(response({ show_reminder: true })) })
    expect(result.current.visible).toBe(false)
    expect(localStorage.getItem('studio-reminder:curator:b')).toBeNull()
  })

  it('ignores provider failure without preventing another chat check', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(response(settings)).mockRejectedValue(new Error('network unavailable'))
    vi.stubGlobal('fetch', fetch)
    const { result } = renderHook(() => useStudioReminder('curator', 'chat'))
    await act(async () => {})
    act(() => result.current.screen('change the prompt', []))
    await act(async () => {})
    expect(result.current.visible).toBe(false)
    expect(localStorage.getItem('studio-reminder:curator:chat')).toBeNull()
  })
})
