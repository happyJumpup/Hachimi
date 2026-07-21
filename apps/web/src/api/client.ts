import type { AccessSession, AnalysisRun, SourceSummary } from '@/domain/types'

export interface AnalysisClient {
  listSources(): Promise<SourceSummary[]>
  createRun(sourceId: string): Promise<AnalysisRun>
  getRun(runId: string): Promise<AnalysisRun>
  cancelRun(runId: string): Promise<AnalysisRun>
}

export interface AccessClient {
  getSession(): Promise<AccessSession>
  upgrade(accessCode: string): Promise<AccessSession>
}

export interface AnalysisEventStream {
  close(): void
}

export interface AnalysisEventStreamFactory {
  open(
    runId: string,
    onEvent: (type: string, data: Record<string, unknown>) => void,
    onError?: () => void,
  ): AnalysisEventStream
}

export class AnalysisApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly retryAfterSeconds: number | null = null,
  ) {
    super(message)
  }
}

const request = async <T>(path: string, init?: RequestInit): Promise<T> => {
  const response = await fetch(path, {
    ...init,
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      ...init?.headers,
    },
  })
  if (!response.ok) {
    let message = '请求失败，请稍后重试'
    try {
      const payload = (await response.json()) as { detail?: string }
      if (payload.detail) message = payload.detail
    } catch {
      // Keep the user-safe fallback; do not expose provider bodies.
    }
    const retryAfterHeader = response.headers.get('Retry-After')
    const parsedRetryAfter = retryAfterHeader === null ? Number.NaN : Number(retryAfterHeader)
    const retryAfterSeconds = Number.isInteger(parsedRetryAfter) && parsedRetryAfter > 0
      ? parsedRetryAfter
      : null
    throw new AnalysisApiError(message, response.status, retryAfterSeconds)
  }
  return response.json() as Promise<T>
}

export const analysisClient: AnalysisClient = {
  listSources: () => request<SourceSummary[]>('/api/v1/sources'),
  createRun: (sourceId) =>
    request<AnalysisRun>('/api/v1/analysis-runs', {
      method: 'POST',
      body: JSON.stringify({ source_id: sourceId }),
    }),
  getRun: (runId) => request<AnalysisRun>(`/api/v1/analysis-runs/${runId}`),
  cancelRun: (runId) =>
    request<AnalysisRun>(`/api/v1/analysis-runs/${runId}`, { method: 'DELETE' }),
}

export const accessClient: AccessClient = {
  getSession: () => request<AccessSession>('/api/v1/access/session'),
  upgrade: (accessCode) => request<AccessSession>('/api/v1/access/session', {
    method: 'POST',
    body: JSON.stringify({ access_code: accessCode }),
  }),
}

const eventNames = [
  'run.started',
  'stage.changed',
  'branch.started',
  'branch.completed',
  'run.completed',
  'run.failed',
  'run.cancelled',
] as const

export const browserEventStreamFactory: AnalysisEventStreamFactory = {
  open(runId, onEvent, onError) {
    const eventSource = new EventSource(`/api/v1/analysis-runs/${runId}/events`)
    for (const eventName of eventNames) {
      eventSource.addEventListener(eventName, (event) => {
        try {
          const payload = JSON.parse((event as MessageEvent<string>).data) as {
            data?: Record<string, unknown>
          }
          onEvent(eventName, payload.data ?? {})
        } catch {
          onError?.()
        }
      })
    }
    eventSource.onerror = () => onError?.()
    return { close: () => eventSource.close() }
  },
}
