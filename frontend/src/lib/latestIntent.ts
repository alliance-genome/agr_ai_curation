export interface LatestIntentOperation {
  readonly generation: number
  readonly token: string
  readonly signal: AbortSignal
  ownsLatest(): boolean
}

/** Owns one latest-wins async operation family. */
export class LatestIntent {
  private static nextToken = 0
  private generation = 0
  private controller: AbortController | null = null

  begin(): LatestIntentOperation {
    this.controller?.abort()
    const generation = ++this.generation
    const controller = new AbortController()
    const token = `latest-intent-${Date.now()}-${++LatestIntent.nextToken}`
    this.controller = controller

    return {
      generation,
      token,
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
