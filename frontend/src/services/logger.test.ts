import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

type LoggerModule = typeof import('./logger')

describe('logger.flush', () => {
  let logger: LoggerModule['logger']
  let originalNodeEnv: string | undefined
  let consoleGroupCollapsedSpy: ReturnType<typeof vi.spyOn>
  let consoleLogSpy: ReturnType<typeof vi.spyOn>
  let consoleGroupEndSpy: ReturnType<typeof vi.spyOn>

  beforeEach(async () => {
    vi.resetModules()
    vi.useFakeTimers()
    vi.mocked(global.fetch).mockReset()

    originalNodeEnv = process.env.NODE_ENV
    process.env.NODE_ENV = 'production'

    vi.spyOn(window, 'addEventListener').mockImplementation(() => undefined)
    consoleGroupCollapsedSpy = vi
      .spyOn(console, 'groupCollapsed')
      .mockImplementation(() => undefined)
    consoleLogSpy = vi.spyOn(console, 'log').mockImplementation(() => undefined)
    consoleGroupEndSpy = vi.spyOn(console, 'groupEnd').mockImplementation(() => undefined)

    logger = (await import('./logger')).logger
  })

  afterEach(() => {
    logger.destroy()
    process.env.NODE_ENV = originalNodeEnv
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('keeps buffered production logs local instead of posting to a removed upload endpoint', async () => {
    logger.info('Buffered production log', { component: 'LoggerFlushTest' })

    await logger.flush()
    await logger.flush()

    expect(vi.mocked(global.fetch)).not.toHaveBeenCalled()
    expect(consoleGroupCollapsedSpy).toHaveBeenCalledTimes(1)
    expect(consoleGroupCollapsedSpy).toHaveBeenCalledWith(
      expect.stringContaining('Buffered production log'),
      expect.any(String)
    )
    expect(consoleLogSpy).toHaveBeenCalledWith(
      'Context:',
      expect.objectContaining({ component: 'LoggerFlushTest' })
    )
    expect(consoleGroupEndSpy).toHaveBeenCalledTimes(1)
  })

  it('returns early when nothing is buffered', async () => {
    await logger.flush()

    expect(vi.mocked(global.fetch)).not.toHaveBeenCalled()
    expect(consoleGroupCollapsedSpy).not.toHaveBeenCalled()
    expect(consoleLogSpy).not.toHaveBeenCalled()
  })
})
