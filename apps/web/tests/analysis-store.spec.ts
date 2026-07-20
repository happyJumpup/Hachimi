import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'

import {
  AnalysisApiError,
  type AnalysisClient,
  type AnalysisEventStream,
  type AnalysisEventStreamFactory,
} from '@/api/client'
import type { AnalysisRun, SourceSummary } from '@/domain/types'
import { useAnalysisStore } from '@/stores/analysis'

const completedRun = (id: string): AnalysisRun => ({
  id,
  source_id: 'video-a',
  trigger_seconds: 45,
  status: 'completed',
  stage: 'completed',
  candidates: [
    {
      id: 'candidate-1',
      name: '拖拽弯举',
      source_id: 'video-a',
      segment: { start_seconds: 41, end_seconds: 51 },
      parameters: {
        mode: 'reps',
        sets: null,
        reps: null,
        duration_seconds: null,
        rest_seconds: null,
      },
      evidence: [{ type: 'visual', start_seconds: 41, end_seconds: 51 }],
      needs_confirmation: true,
    },
  ],
  warnings: [],
  empty_reason: null,
  error: null,
  created_at: '2026-07-20T00:00:00Z',
  updated_at: '2026-07-20T00:00:01Z',
})

class FakeClient implements AnalysisClient {
  cancelled: string[] = []

  async listSources(): Promise<SourceSummary[]> {
    return []
  }

  async createRun(): Promise<AnalysisRun> {
    return { ...completedRun('run-1'), status: 'queued', stage: 'queued', candidates: [] }
  }

  async getRun(runId: string): Promise<AnalysisRun> {
    return completedRun(runId)
  }

  async cancelRun(runId: string): Promise<AnalysisRun> {
    this.cancelled.push(runId)
    return { ...completedRun(runId), status: 'cancelled', stage: 'cancelled', candidates: [] }
  }
}

class FakeEventFactory implements AnalysisEventStreamFactory {
  onEvent?: (type: string, data: Record<string, unknown>) => void
  closed = false

  open(
    runId: string,
    onEvent: (type: string, data: Record<string, unknown>) => void,
  ): AnalysisEventStream {
    this.onEvent = onEvent
    return {
      close: () => {
        this.closed = true
      },
    }
  }

  emit(type: string, data: Record<string, unknown> = {}): void {
    this.onEvent?.(type, data)
  }
}

const flushPromises = async (): Promise<void> => {
  await Promise.resolve()
  await Promise.resolve()
}

describe('动作分析 store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('shows real stages and accepts candidates only from the active run', async () => {
    const client = new FakeClient()
    const events = new FakeEventFactory()
    const store = useAnalysisStore()

    await store.start({ sourceId: 'video-a', triggerSeconds: 45, client, events })
    events.emit('stage.changed', { stage: 'analyzing_evidence' })
    expect(store.stage).toBe('analyzing_evidence')

    events.emit('run.completed')
    await flushPromises()

    expect(store.status).toBe('completed')
    expect(store.candidates[0].name).toBe('拖拽弯举')
    expect(events.closed).toBe(true)
  })

  it('cancels the active run and discards late completion events', async () => {
    const client = new FakeClient()
    const events = new FakeEventFactory()
    const store = useAnalysisStore()

    await store.start({ sourceId: 'video-a', triggerSeconds: 45, client, events })
    await store.cancel(client)
    events.emit('run.completed')
    await flushPromises()

    expect(client.cancelled).toEqual(['run-1'])
    expect(store.status).toBe('cancelled')
    expect(store.candidates).toEqual([])
  })

  it('cancels a run that is created after the user already cancelled', async () => {
    const client = new FakeClient()
    const events = new FakeEventFactory()
    const store = useAnalysisStore()
    let resolveCreated!: (run: AnalysisRun) => void
    client.createRun = () =>
      new Promise<AnalysisRun>((resolve) => {
        resolveCreated = resolve
      })

    const starting = store.start({ sourceId: 'video-a', triggerSeconds: 45, client, events })
    await flushPromises()
    await store.cancel(client)
    resolveCreated({
      ...completedRun('run-created-late'),
      status: 'queued',
      stage: 'queued',
      candidates: [],
    })
    await starting

    expect(client.cancelled).toEqual(['run-created-late'])
    expect(store.status).toBe('cancelled')
    expect(store.candidates).toEqual([])
  })

  it('reports capacity exhaustion separately from provider failure', async () => {
    const client = new FakeClient()
    const events = new FakeEventFactory()
    const store = useAnalysisStore()
    client.createRun = async () => {
      throw new AnalysisApiError('真实动作分析暂时繁忙，请稍后重试', 429, 15)
    }

    await store.start({ sourceId: 'video-a', triggerSeconds: 45, client, events })

    expect(store.status).toBe('failed')
    expect(store.failureKind).toBe('capacity')
    expect(store.retryAfterSeconds).toBe(15)
    expect(store.error?.message).toBe('真实动作分析名额正在使用中')
  })
})
