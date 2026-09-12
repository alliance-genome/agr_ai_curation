export interface LatestIntentOperation {
  readonly owner: string
  readonly generation: number
  readonly signal: AbortSignal
  ownsLatest(): boolean
}

/** Owns one latest-wins async operation family. */
export class LatestIntent {
  private static readonly owner = `latest-intent-${Date.now()}-${Math.random().toString(36).slice(2)}`
  private static nextGeneration = Date.now() * 1000
  private generation = 0
  private controller: AbortController | null = null

  begin(): LatestIntentOperation {
    this.controller?.abort()
    // Order user actions by when they happen, not when their browser tab opened.
    // Retain a monotonic counter for same-millisecond actions and clock rollback.
    const generation = Math.max(Date.now() * 1000, LatestIntent.nextGeneration + 1)
    LatestIntent.nextGeneration = generation
    this.generation = generation
    const controller = new AbortController()
    this.controller = controller

    return {
      owner: LatestIntent.owner,
      generation,
      signal: controller.signal,
      ownsLatest: () => (
        this.generation === generation
        && this.controller === controller
        && !controller.signal.aborted
      ),
    }
  }

  invalidate(): void {
    this.generation += 1
    this.controller?.abort()
    this.controller = null
  }
}
