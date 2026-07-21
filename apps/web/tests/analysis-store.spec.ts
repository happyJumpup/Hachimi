import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'

import {
  AnalysisApiError,
  type AnalysisClient,
  type AnalysisEventStream,
  type AnalysisEventStreamFactory,
} from '@/api/client'
import type { AnalysisCheckpointStore } from '@/stores/analysis-persistence'
import type { AnalysisRun, CoverageGap, SourceSummary } from '@/domain/types'
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
  source_duration_seconds: 60,
  processed_seconds: 60,
  discovered_candidate_count: 1,
  coverage_status: 'complete',
  coverage_gaps: [],
  created_at: '2026-07-20T00:00:00Z',
  updated_at: '2026-07-20T00:00:01Z',
})

class FakeClient implements AnalysisClient {
  cancelled: string[] = []

  async getCapabilities() {
    return {
      local_upload_enabled: true,
      local_analysis_max_seconds: 60,
      local_upload_max_bytes: 25_000_000,
    }
  }

  async listSources(): Promise<SourceSummary[]> {
    return []
  }

  async createRun(): Promise<AnalysisRun> {
    return { ...completedRun('run-1'), status: 'queued', stage: 'queued', candidates: [] }
  }

  async createLocalRun(): Promise<AnalysisRun> {
    return this.createRun()
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
  openedRunId: string | null = null

  open(
    runId: string,
    onEvent: (type: string, data: Record<string, unknown>) => void,
  ): AnalysisEventStream {
    this.openedRunId = runId
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

class MemoryCheckpoints implements AnalysisCheckpointStore {
  value = null as ReturnType<AnalysisCheckpointStore['load']>

  load() { return this.value }
  save(value: NonNullable<ReturnType<AnalysisCheckpointStore['load']>>) { this.value = structuredClone(value) }
  clear() { this.value = null }
}

const flushPromises = async (): Promise<void> => {
  await Promise.resolve()
  await Promise.resolve()
}

describe('动作分析 store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('turns a source-list failure into a safe retryable state', async () => {
    const client = new FakeClient()
    client.listSources = async () => {
      throw new Error('internal source manifest path should not leak')
    }
    const store = useAnalysisStore()

    await store.loadSources(client)

    expect(store.sourcesLoading).toBe(false)
    expect(store.sources).toEqual([])
    expect(store.sourcesError).toBe('视频暂时没有读取成功，请重试')

    client.listSources = async () => [{
      id: 'video-a',
      title: '来源视频 A',
      media_url: '/api/v1/sources/video-a/media',
      duration_seconds: 60,
      origin_url: null,
    }]
    await store.loadSources(client)
    expect(store.sourcesError).toBeNull()
    expect(store.sources).toHaveLength(1)
  })

  it('shows real stages and accepts candidates only from the active run', async () => {
    const client = new FakeClient()
    const events = new FakeEventFactory()
    const store = useAnalysisStore()

    await store.start({ sourceId: 'video-a', client, events })
    events.emit('stage.changed', { stage: 'analyzing_evidence' })
    expect(store.stage).toBe('analyzing_evidence')

    events.emit('run.completed')
    await flushPromises()

    expect(store.status).toBe('completed')
    expect(store.candidates[0].name).toBe('拖拽弯举')
    expect(events.closed).toBe(true)
  })

  it('uses real progress snapshots without inventing a percentage or ETA', async () => {
    const client = new FakeClient()
    const events = new FakeEventFactory()
    const store = useAnalysisStore()

    await store.start({ sourceId: 'video-a', client, events })
    events.emit('stage.changed', {
      stage: 'analyzing_evidence',
      source_duration_seconds: 120,
      processed_seconds: 37,
      discovered_candidate_count: 2,
    })

    expect(store.sourceDurationSeconds).toBe(120)
    expect(store.processedSeconds).toBe(37)
    expect(store.discoveredCandidateCount).toBe(2)
  })

  it('keeps the backend local-media limit visible and refreshes capabilities', async () => {
    const client = new FakeClient()
    let capabilityReads = 0
    client.getCapabilities = async () => {
      capabilityReads += 1
      return {
        local_upload_enabled: true,
        local_analysis_max_seconds: 45,
        local_upload_max_bytes: 10_000_000,
      }
    }
    client.createLocalRun = async () => {
      throw new AnalysisApiError('当前环境支持最长 45 秒的视频', 422)
    }
    const store = useAnalysisStore()

    await store.start({
      sourceId: 'local:gate-test',
      file: new Blob(['video'], { type: 'video/mp4' }),
      client,
      events: new FakeEventFactory(),
    })
    await flushPromises()

    expect(store.status).toBe('failed')
    expect(store.error?.message).toBe('当前环境支持最长 45 秒的视频')
    expect(capabilityReads).toBe(1)
    expect(store.capabilities?.local_analysis_max_seconds).toBe(45)
  })

  it('restores a persisted run with GET and reconnects its event stream', async () => {
    const client = new FakeClient()
    const firstEvents = new FakeEventFactory()
    const persistence = new MemoryCheckpoints()
    const first = useAnalysisStore()
    await first.start({ sourceId: 'video-a', client, events: firstEvents, persistence })

    setActivePinia(createPinia())
    client.getRun = async (runId) => ({
      ...completedRun(runId),
      status: 'running',
      stage: 'analyzing_evidence',
      candidates: [],
      processed_seconds: 18,
      discovered_candidate_count: 1,
      coverage_status: null,
    })
    const restoredEvents = new FakeEventFactory()
    const restored = useAnalysisStore()

    await restored.restore({ client, events: restoredEvents, persistence })

    expect(restored.activeRunId).toBe('run-1')
    expect(restored.status).toBe('running')
    expect(restored.processedSeconds).toBe(18)
    expect(restoredEvents.openedRunId).toBe('run-1')
  })

  it('retries one local coverage gap and merges candidates in source order', async () => {
    const client = new FakeClient()
    const events = new FakeEventFactory()
    const persistence = new MemoryCheckpoints()
    const sourceId = 'local:11111111-1111-4111-8111-111111111111'
    const gap: CoverageGap = {
      start_seconds: 20,
      end_seconds: 30,
      reason: 'provider_error',
      retryable: true,
    }
    let createCount = 0
    client.createLocalRun = async () => {
      createCount += 1
      return {
        ...completedRun(createCount === 1 ? 'run-root' : 'run-gap'),
        source_id: sourceId,
        status: 'queued',
        stage: 'queued',
        candidates: [],
        processed_seconds: 0,
        discovered_candidate_count: 0,
        coverage_status: null,
      }
    }
    client.getRun = async (runId) => runId === 'run-root'
      ? {
          ...completedRun(runId),
          source_id: sourceId,
          candidates: [{
            ...completedRun(runId).candidates[0]!,
            id: 'candidate-1',
            source_id: sourceId,
            segment: { start_seconds: 40, end_seconds: 50 },
          }],
          processed_seconds: 50,
          coverage_status: 'partial',
          coverage_gaps: [gap],
        }
      : {
          ...completedRun(runId),
          source_id: sourceId,
          candidates: [{
            ...completedRun(runId).candidates[0]!,
            id: 'candidate-1',
            source_id: sourceId,
            segment: { start_seconds: 22, end_seconds: 28 },
          }],
          processed_seconds: 10,
        }
    const store = useAnalysisStore()
    const media = new File(['video'], '训练.mp4', { type: 'video/mp4' })

    await store.start({ sourceId, file: media, client, events, persistence })
    events.emit('run.completed')
    await flushPromises()
    expect(store.coverageGaps).toEqual([gap])

    await store.retryGap({ gap, file: media, client, events, persistence })
    events.emit('run.completed')
    await flushPromises()

    expect(store.candidates.map((candidate) => candidate.id)).toEqual([
      'run-gap:candidate-1',
      'candidate-1',
    ])
    expect(store.coverageGaps).toEqual([])
    expect(store.coverageStatus).toBe('complete')
    expect(store.processedSeconds).toBe(60)
    expect(store.gapRetryError).toBeNull()
  })

  it('keeps root progress and its gap after a range retry fails', async () => {
    const client = new FakeClient()
    const events = new FakeEventFactory()
    const persistence = new MemoryCheckpoints()
    const sourceId = 'local:11111111-1111-4111-8111-111111111111'
    const gap: CoverageGap = {
      start_seconds: 20,
      end_seconds: 30,
      reason: 'timeout',
      retryable: true,
    }
    let createCount = 0
    client.createLocalRun = async () => ({
      ...completedRun(++createCount === 1 ? 'run-root' : 'run-gap'),
      source_id: sourceId,
      status: 'queued',
      stage: 'queued',
      candidates: [],
      processed_seconds: 0,
      discovered_candidate_count: 0,
      coverage_status: null,
    })
    client.getRun = async (runId) => runId === 'run-root'
      ? {
          ...completedRun(runId),
          source_id: sourceId,
          processed_seconds: 50,
          coverage_status: 'partial',
          coverage_gaps: [gap],
        }
      : {
          ...completedRun(runId),
          source_id: sourceId,
          status: 'failed',
          stage: 'failed',
          candidates: [],
          processed_seconds: 4,
          discovered_candidate_count: 0,
          coverage_status: null,
          error: { code: 'timeout', message: '区间分析超时', retryable: true },
        }
    const store = useAnalysisStore()
    const media = new File(['video'], '训练.mp4', { type: 'video/mp4' })

    await store.start({ sourceId, file: media, client, events, persistence })
    events.emit('run.completed')
    await flushPromises()
    await store.retryGap({ gap, file: media, client, events, persistence })
    events.emit('run.failed')
    await flushPromises()

    expect(store.processedSeconds).toBe(50)
    expect(store.discoveredCandidateCount).toBe(1)
    expect(store.coverageGaps).toEqual([gap])
    expect(store.gapRetryError).toContain('缺口已保留')
  })

  it('restores source progress when a range retry is rejected immediately', async () => {
    const client = new FakeClient()
    const events = new FakeEventFactory()
    const persistence = new MemoryCheckpoints()
    const sourceId = 'local:immediate-failure'
    const gap: CoverageGap = {
      start_seconds: 20,
      end_seconds: 30,
      reason: 'timeout',
      retryable: true,
    }
    let createCount = 0
    client.createLocalRun = async () => {
      createCount += 1
      if (createCount === 1) {
        return {
          ...completedRun('run-root'),
          source_id: sourceId,
          status: 'queued',
          stage: 'queued',
          candidates: [],
          processed_seconds: 0,
          discovered_candidate_count: 0,
          coverage_status: null,
        }
      }
      return {
        ...completedRun('run-gap'),
        source_id: sourceId,
        status: 'failed',
        stage: 'failed',
        candidates: [],
        processed_seconds: 3,
        discovered_candidate_count: 0,
        coverage_status: null,
        error: { code: 'timeout', message: '区间分析超时', retryable: true },
      }
    }
    client.getRun = async (runId) => ({
      ...completedRun(runId),
      source_id: sourceId,
      processed_seconds: 50,
      coverage_status: 'partial',
      coverage_gaps: [gap],
    })
    const store = useAnalysisStore()
    const media = new File(['video'], '训练.mp4', { type: 'video/mp4' })

    await store.start({ sourceId, file: media, client, events, persistence })
    events.emit('run.completed')
    await flushPromises()
    await store.retryGap({ gap, file: media, client, events, persistence })

    expect(store.processedSeconds).toBe(50)
    expect(store.coverageGaps).toEqual([gap])
    expect(store.gapRetryError).toContain('缺口已保留')
  })

  it('cancels the active run and discards late completion events', async () => {
    const client = new FakeClient()
    const events = new FakeEventFactory()
    const store = useAnalysisStore()

    await store.start({ sourceId: 'video-a', client, events })
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

    const starting = store.start({ sourceId: 'video-a', client, events })
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

    await store.start({ sourceId: 'video-a', client, events })

    expect(store.status).toBe('failed')
    expect(store.failureKind).toBe('capacity')
    expect(store.retryAfterSeconds).toBe(15)
    expect(store.error?.message).toBe('真实动作分析名额正在使用中')
  })

  it('does not expose technical details when creating an analysis run fails', async () => {
    const client = new FakeClient()
    const events = new FakeEventFactory()
    const store = useAnalysisStore()
    client.createRun = async () => {
      throw new Error('Failed to fetch C:\\private\\media\\video.mp4')
    }

    await store.start({ sourceId: 'video-a', client, events })

    expect(store.status).toBe('failed')
    expect(store.failureKind).toBe('system')
    expect(store.error?.message).toBe('动作分析暂时不可用，请重试')
    expect(store.error?.message).not.toContain('private')
  })

  it('keeps a recoverable run id when a result refresh is temporarily unavailable', async () => {
    const client = new FakeClient()
    const events = new FakeEventFactory()
    const store = useAnalysisStore()
    client.getRun = async () => {
      throw new Error('temporary result read failure')
    }

    await store.start({ sourceId: 'video-a', client, events })
    events.emit('run.completed')
    await flushPromises()

    expect(store.status).toBe('queued')
    expect(store.activeRunId).toBe('run-1')
    expect(store.restoreError).toContain('连接暂时中断')
    expect(events.closed).toBe(false)
  })
})
