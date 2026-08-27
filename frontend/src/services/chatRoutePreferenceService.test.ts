import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  clearChatRoutePreference,
  fetchChatRoutePreference,
  fetchChatRouteTargets,
  saveChatRoutePreference,
} from './chatRoutePreferenceService'

const mockFetch = vi.fn()
global.fetch = mockFetch

const automatic = {
  mode: 'automatic', agent_id: null, flow_id: null, status: 'available', target: null,
}

describe('chatRoutePreferenceService', () => {
  beforeEach(() => vi.clearAllMocks())

  it('reads the confirmed preference with authenticated cookies', async () => {
    mockFetch.mockResolvedValue(new Response(JSON.stringify(automatic), { status: 200 }))
    await expect(fetchChatRoutePreference()).resolves.toEqual(automatic)
    expect(mockFetch).toHaveBeenCalledWith('/api/users/me/chat-route-preference', {
      credentials: 'include',
    })
  })

  it('reads only the server-authorized picker targets', async () => {
    const targets = [{ id: 'agent-1', kind: 'agent', display_name: 'Agent One', available: true }]
    mockFetch.mockResolvedValue(new Response(JSON.stringify({ targets }), { status: 200 }))
    await expect(fetchChatRouteTargets()).resolves.toEqual(targets)
    expect(mockFetch).toHaveBeenCalledWith('/api/users/me/chat-route-targets', {
      credentials: 'include',
    })
  })

  it('atomically replaces an agent preference with a complete payload', async () => {
    mockFetch.mockResolvedValue(new Response(JSON.stringify(automatic), { status: 200 }))
    const update = { mode: 'agent' as const, agent_id: 'agent-1', flow_id: null }
    await saveChatRoutePreference(update)
    expect(mockFetch).toHaveBeenCalledWith('/api/users/me/chat-route-preference', {
      method: 'PUT',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(update),
    })
  })

  it('clears the persisted target through the canonical delete endpoint', async () => {
    mockFetch.mockResolvedValue(new Response(JSON.stringify(automatic), { status: 200 }))
    await clearChatRoutePreference()
    expect(mockFetch).toHaveBeenCalledWith('/api/users/me/chat-route-preference', {
      method: 'DELETE',
      credentials: 'include',
    })
  })

  it('rejects failed responses', async () => {
    mockFetch.mockResolvedValue(new Response('', { status: 500 }))
    await expect(fetchChatRoutePreference()).rejects.toThrow('Could not load your chat default.')
  })
})
