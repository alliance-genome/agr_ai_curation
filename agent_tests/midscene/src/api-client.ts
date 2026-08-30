import { compactEvidence, redactSecrets, sanitizeHeaders } from './redaction.js'

export interface ApiEvidence {
  at: string
  method: string
  path: string
  status: number
  request?: unknown
  response?: unknown
}

export interface ApiClientOptions {
  baseUrl: string
  authMode: 'api-key' | 'cookie'
  secret: string
  timeoutMs: number
  evidence?: ApiEvidence[]
  evidencePreviewChars?: number
  fetchImpl?: typeof fetch
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly method: string,
    readonly path: string,
    readonly status: number,
    readonly body: unknown,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export class ApiClient {
  readonly #baseUrl: string
  readonly #authMode: 'api-key' | 'cookie'
  readonly #secret: string
  readonly #timeoutMs: number
  readonly #evidence: ApiEvidence[]
  readonly #fetch: typeof fetch
  readonly #evidencePreviewChars: number

  constructor(options: ApiClientOptions) {
    this.#baseUrl = options.baseUrl.replace(/\/$/, '')
    this.#authMode = options.authMode
    this.#secret = options.secret
    this.#timeoutMs = options.timeoutMs
    this.#evidence = options.evidence ?? []
    this.#fetch = options.fetchImpl ?? fetch
    this.#evidencePreviewChars = options.evidencePreviewChars ?? 4_000
  }

  get evidence(): readonly ApiEvidence[] {
    return this.#evidence
  }

  async request<T>(method: string, path: string, options: { body?: unknown; headers?: Record<string, string> } = {}): Promise<T> {
    const headers: Record<string, string> = { Accept: 'application/json', ...options.headers }
    if (options.body !== undefined && !headers['Content-Type']) headers['Content-Type'] = 'application/json'
    if (this.#authMode === 'api-key') headers['X-API-Key'] = this.#secret
    else headers.Cookie = this.#secret

    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(new Error(`API request timed out after ${this.#timeoutMs}ms`)), this.#timeoutMs)
    let response: Response
    try {
      const requestInit: RequestInit = {
        method,
        headers,
        signal: controller.signal,
      }
      if (options.body !== undefined) {
        requestInit.body = headers['Content-Type'] === 'application/json'
          ? JSON.stringify(options.body)
          : options.body as BodyInit
      }
      response = await this.#fetch(`${this.#baseUrl}${path}`, requestInit)
    } finally {
      clearTimeout(timeout)
    }
    const contentType = response.headers.get('content-type') ?? ''
    const text = await response.text()
    let responseBody: unknown = text
    if (text && contentType.includes('json')) {
      try { responseBody = JSON.parse(text) } catch { responseBody = text }
    } else if (!text) responseBody = null

    this.#evidence.push({
      at: new Date().toISOString(),
      method,
      path,
      status: response.status,
      ...(options.body === undefined ? {} : { request: redactSecrets(options.body) }),
      response: compactEvidence(responseBody, this.#evidencePreviewChars),
    })

    if (!response.ok) {
      throw new ApiError(
        `${method} ${path} failed with ${response.status}: ${JSON.stringify(redactSecrets(responseBody))}`,
        method,
        path,
        response.status,
        redactSecrets(responseBody),
      )
    }
    return responseBody as T
  }

  get<T>(path: string): Promise<T> { return this.request<T>('GET', path) }
  post<T>(path: string, body?: unknown): Promise<T> { return this.request<T>('POST', path, { body }) }
  put<T>(path: string, body?: unknown): Promise<T> { return this.request<T>('PUT', path, { body }) }
  delete<T>(path: string): Promise<T> { return this.request<T>('DELETE', path) }

  async download(path: string): Promise<Uint8Array> {
    const headers: Record<string, string> = { Accept: 'application/octet-stream, application/json' }
    if (this.#authMode === 'api-key') headers['X-API-Key'] = this.#secret
    else headers.Cookie = this.#secret
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(new Error(`API request timed out after ${this.#timeoutMs}ms`)), this.#timeoutMs)
    let response: Response
    try {
      response = await this.#fetch(`${this.#baseUrl}${path}`, { method: 'GET', headers, signal: controller.signal })
    } finally {
      clearTimeout(timeout)
    }
    const bytes = new Uint8Array(await response.arrayBuffer())
    this.#evidence.push({
      at: new Date().toISOString(), method: 'GET', path, status: response.status,
      response: { size_bytes: bytes.length, content_type: response.headers.get('content-type') },
    })
    if (!response.ok) {
      const body = redactSecrets(new TextDecoder().decode(bytes).slice(0, this.#evidencePreviewChars))
      throw new ApiError(`GET ${path} failed with ${response.status}: ${JSON.stringify(body)}`, 'GET', path, response.status, body)
    }
    return bytes
  }

  async postForm<T>(path: string, form: FormData, evidenceRequest: unknown): Promise<T> {
    const headers: Record<string, string> = { Accept: 'application/json' }
    if (this.#authMode === 'api-key') headers['X-API-Key'] = this.#secret
    else headers.Cookie = this.#secret
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(new Error(`API request timed out after ${this.#timeoutMs}ms`)), this.#timeoutMs)
    let response: Response
    try {
      response = await this.#fetch(`${this.#baseUrl}${path}`, {
        method: 'POST', headers, body: form, signal: controller.signal,
      })
    } finally {
      clearTimeout(timeout)
    }
    const text = await response.text()
    let body: unknown = text
    try { body = text ? JSON.parse(text) : null } catch { body = text }
    this.#evidence.push({
      at: new Date().toISOString(), method: 'POST', path, status: response.status,
      request: redactSecrets(evidenceRequest), response: compactEvidence(body, this.#evidencePreviewChars),
    })
    if (!response.ok) {
      throw new ApiError(
        `POST ${path} failed with ${response.status}: ${JSON.stringify(redactSecrets(body))}`,
        'POST', path, response.status, redactSecrets(body),
      )
    }
    return body as T
  }

  authHeaders(): Record<string, string> {
    return sanitizeHeaders(this.#authMode === 'api-key'
      ? { 'X-API-Key': this.#secret }
      : { Cookie: this.#secret })
  }
}
