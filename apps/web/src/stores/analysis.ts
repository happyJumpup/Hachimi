import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import {
  AnalysisApiError,
  type AnalysisClient,
  type AnalysisEventStream,
  type AnalysisEventStreamFactory,
} from '@/api/client'
import type {
  AnalysisCandidate,
  AnalysisError,
  AnalysisRun,
  AnalysisWarning,
  SourceSummary,
} from '@/domain/types'

type UiStatus = 'idle' | AnalysisRun['status']

const stageCopy: Record<AnalysisRun['stage'], string> = {
  queued: '正在建立分析请求',
  preparing_media: '正在准备当前片段',
  analyzing_evidence: '正在听讲解、看动作',
  expanding_window: '正在扩大附近范围',
  fusing_candidates: '正在整理动作候选',
  completed: '候选已准备好',
  failed: '这次没有分析成功',
  cancelled: '已取消分析',
}

export const useAnalysisStore = defineStore('analysis', () => {
  const sources = ref<SourceSummary[]>([])
  const sourcesLoading = ref(false)
  const status = ref<UiStatus>('idle')
  const stage = ref<AnalysisRun['stage']>('queued')
  const candidates = ref<AnalysisCandidate[]>([])
  const warnings = ref<AnalysisWarning[]>([])
  const error = ref<AnalysisError | null>(null)
  const emptyReason = ref<'no_evidence' | null>(null)
  const activeRunId = ref<string | null>(null)
  const failureKind = ref<'capacity' | 'system' | null>(null)
  const retryAfterSeconds = ref<number | null>(null)
  let stream: AnalysisEventStream | undefined
  let generation = 0

  const isRunning = computed(() => status.value === 'queued' || status.value === 'running')
  const stageLabel = computed(() => stageCopy[stage.value])

  async function loadSources(client: AnalysisClient): Promise<void> {
    sourcesLoading.value = true
    try {
      sources.value = await client.listSources()
    } finally {
      sourcesLoading.value = false
    }
  }

  async function start(input: {
    sourceId: string
    triggerSeconds: number
    client: AnalysisClient
    events: AnalysisEventStreamFactory
  }): Promise<void> {
    if (activeRunId.value) {
      await cancel(input.client)
    }
    const currentGeneration = ++generation
    candidates.value = []
    warnings.value = []
    error.value = null
    emptyReason.value = null
    failureKind.value = null
    retryAfterSeconds.value = null
    status.value = 'queued'
    stage.value = 'queued'

    try {
      const created = await input.client.createRun(input.sourceId, input.triggerSeconds)
      if (generation !== currentGeneration) {
        await input.client.cancelRun(created.id).catch(() => undefined)
        return
      }
      activeRunId.value = created.id
      status.value = created.status
      stage.value = created.stage
      stream = input.events.open(
        created.id,
        (type, data) => {
          if (generation !== currentGeneration || activeRunId.value !== created.id) return
          const nextStage = data.stage
          if (typeof nextStage === 'string' && nextStage in stageCopy) {
            stage.value = nextStage as AnalysisRun['stage']
          }
          if (type === 'run.completed' || type === 'run.failed' || type === 'run.cancelled') {
            void refresh(created.id, currentGeneration, input.client)
          }
        },
        () => {
          if (generation === currentGeneration && activeRunId.value === created.id) {
            void refresh(created.id, currentGeneration, input.client)
          }
        },
      )
    } catch (caught) {
      if (generation !== currentGeneration) return
      status.value = 'failed'
      stage.value = 'failed'
      const capacityError = caught instanceof AnalysisApiError && caught.status === 429
      failureKind.value = capacityError ? 'capacity' : 'system'
      retryAfterSeconds.value = capacityError ? caught.retryAfterSeconds : null
      error.value = {
        code: 'provider_error',
        message: capacityError
          ? '真实动作分析名额正在使用中'
          : caught instanceof Error ? caught.message : '动作分析暂时不可用',
        retryable: true,
      }
    }
  }

  async function refresh(
    runId: string,
    currentGeneration: number,
    client: AnalysisClient,
  ): Promise<void> {
    try {
      const run = await client.getRun(runId)
      if (generation !== currentGeneration || activeRunId.value !== runId) return
      applyRun(run)
      if (['completed', 'failed', 'cancelled'].includes(run.status)) {
        stream?.close()
        stream = undefined
        activeRunId.value = null
      }
    } catch {
      if (generation !== currentGeneration || activeRunId.value !== runId) return
      stream?.close()
      stream = undefined
      activeRunId.value = null
      status.value = 'failed'
      stage.value = 'failed'
      error.value = {
        code: 'provider_error',
        message: '无法读取分析结果，请重试',
        retryable: true,
      }
      failureKind.value = 'system'
    }
  }

  function applyRun(run: AnalysisRun): void {
    status.value = run.status
    stage.value = run.stage
    candidates.value = run.candidates
    warnings.value = run.warnings
    error.value = run.error
    emptyReason.value = run.empty_reason
    failureKind.value = run.status === 'failed' ? 'system' : null
    retryAfterSeconds.value = null
  }

  async function cancel(client: AnalysisClient): Promise<void> {
    const runId = activeRunId.value
    generation += 1
    stream?.close()
    stream = undefined
    activeRunId.value = null
    candidates.value = []
    warnings.value = []
    error.value = null
    emptyReason.value = null
    failureKind.value = null
    retryAfterSeconds.value = null
    if (runId) {
      try {
        await client.cancelRun(runId)
      } finally {
        status.value = 'cancelled'
        stage.value = 'cancelled'
      }
      return
    }
    status.value = 'cancelled'
    stage.value = 'cancelled'
  }

  function clearResult(): void {
    generation += 1
    stream?.close()
    stream = undefined
    activeRunId.value = null
    status.value = 'idle'
    stage.value = 'queued'
    candidates.value = []
    warnings.value = []
    error.value = null
    emptyReason.value = null
    failureKind.value = null
    retryAfterSeconds.value = null
  }

  return {
    sources,
    sourcesLoading,
    status,
    stage,
    stageLabel,
    candidates,
    warnings,
    error,
    emptyReason,
    activeRunId,
    failureKind,
    retryAfterSeconds,
    isRunning,
    loadSources,
    start,
    cancel,
    clearResult,
  }
})
