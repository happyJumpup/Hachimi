import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import {
  AnalysisApiError,
  type AnalysisClient,
  type AnalysisEventStream,
  type AnalysisEventStreamFactory,
} from '@/api/client'
import type {
  AnalysisCapabilities,
  AnalysisCandidate,
  AnalysisError,
  AnalysisRun,
  AnalysisWarning,
  CoverageGap,
  SourceSummary,
} from '@/domain/types'
import {
  browserAnalysisCheckpointStore,
  type AnalysisCheckpoint,
  type AnalysisCheckpointStore,
} from '@/stores/analysis-persistence'

type UiStatus = 'idle' | AnalysisRun['status']
type TransferStatus = 'idle' | 'uploading'

const stageCopy: Record<AnalysisRun['stage'], string> = {
  queued: '正在建立分析请求',
  preparing_media: '视频已上传，正在准备分析',
  analyzing_evidence: '正在听讲解、看动作',
  expanding_window: '正在继续检查相关片段',
  fusing_candidates: '正在整理动作候选',
  completed: '候选已准备好',
  failed: '这次没有分析成功',
  cancelled: '已取消分析',
}

const isActiveStatus = (value: AnalysisRun['status'] | null): boolean =>
  value === 'queued' || value === 'running'

const sameGap = (left: CoverageGap, right: CoverageGap): boolean =>
  left.start_seconds === right.start_seconds && left.end_seconds === right.end_seconds

const mergeCandidates = (
  current: AnalysisCandidate[],
  additions: AnalysisCandidate[],
): AnalysisCandidate[] => {
  const byId = new Map(current.map((candidate) => [candidate.id, candidate]))
  for (const candidate of additions) byId.set(candidate.id, candidate)
  return [...byId.values()].sort((left, right) => (
    left.segment.start_seconds - right.segment.start_seconds
    || left.segment.end_seconds - right.segment.end_seconds
    || left.id.localeCompare(right.id)
  ))
}

const mergeWarnings = (
  current: AnalysisWarning[],
  additions: AnalysisWarning[],
): AnalysisWarning[] => {
  const seen = new Set<string>()
  return [...current, ...additions].filter((warning) => {
    const key = `${warning.code}:${warning.message}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

export const useAnalysisStore = defineStore('analysis', () => {
  const sources = ref<SourceSummary[]>([])
  const sourcesLoading = ref(false)
  const sourcesError = ref<string | null>(null)
  const capabilities = ref<AnalysisCapabilities | null>(null)
  const capabilitiesLoading = ref(false)
  const capabilitiesError = ref<string | null>(null)
  const status = ref<UiStatus>('idle')
  const stage = ref<AnalysisRun['stage']>('queued')
  const transferStatus = ref<TransferStatus>('idle')
  const candidates = ref<AnalysisCandidate[]>([])
  const warnings = ref<AnalysisWarning[]>([])
  const error = ref<AnalysisError | null>(null)
  const emptyReason = ref<'no_evidence' | null>(null)
  const activeRunId = ref<string | null>(null)
  const activeRunStatus = ref<AnalysisRun['status'] | null>(null)
  const currentSourceId = ref<string | null>(null)
  const currentSourceKind = ref<'controlled' | 'local' | null>(null)
  const sourceDurationSeconds = ref(0)
  const processedSeconds = ref(0)
  const sourceCoveredSeconds = ref(0)
  const discoveredCandidateCount = ref(0)
  const coverageStatus = ref<AnalysisRun['coverage_status']>(null)
  const coverageGaps = ref<CoverageGap[]>([])
  const failureKind = ref<'capacity' | 'local_gate' | 'system' | null>(null)
  const retryAfterSeconds = ref<number | null>(null)
  const restoring = ref(false)
  const restoreError = ref<string | null>(null)
  const gapRetrying = ref<CoverageGap | null>(null)
  const gapRetryError = ref<string | null>(null)
  let stream: AnalysisEventStream | undefined
  let generation = 0
  let checkpoint: AnalysisCheckpoint | null = null
  let checkpointStore: AnalysisCheckpointStore = browserAnalysisCheckpointStore

  const isRunning = computed(() => (
    transferStatus.value === 'uploading'
    || status.value === 'queued'
    || status.value === 'running'
    || (
      activeRunId.value !== null && isActiveStatus(activeRunStatus.value)
    )
  ))
  const hasOwnedRun = ref(false)
  const stageLabel = computed(() => (
    transferStatus.value === 'uploading' ? '正在上传本机视频' : stageCopy[stage.value]
  ))

  const usePersistence = (next?: AnalysisCheckpointStore): AnalysisCheckpointStore => {
    if (next) checkpointStore = next
    return checkpointStore
  }

  const persistCheckpoint = (): void => {
    if (checkpoint) checkpointStore.save(checkpoint)
  }

  const closeStream = (): void => {
    stream?.close()
    stream = undefined
  }

  const resetResult = (): void => {
    candidates.value = []
    warnings.value = []
    error.value = null
    emptyReason.value = null
    sourceDurationSeconds.value = 0
    processedSeconds.value = 0
    sourceCoveredSeconds.value = 0
    discoveredCandidateCount.value = 0
    coverageStatus.value = null
    coverageGaps.value = []
    failureKind.value = null
    retryAfterSeconds.value = null
    restoreError.value = null
    gapRetrying.value = null
    gapRetryError.value = null
  }

  async function loadCapabilities(client: AnalysisClient): Promise<void> {
    capabilitiesLoading.value = true
    capabilitiesError.value = null
    try {
      capabilities.value = await client.getCapabilities()
    } catch {
      capabilities.value = null
      capabilitiesError.value = '当前环境的本地视频能力暂时无法确认'
    } finally {
      capabilitiesLoading.value = false
    }
  }

  async function loadSources(client: AnalysisClient): Promise<void> {
    sourcesLoading.value = true
    sourcesError.value = null
    try {
      sources.value = await client.listSources()
    } catch {
      sourcesError.value = '视频暂时没有读取成功，请重试'
    } finally {
      sourcesLoading.value = false
    }
  }

  const applyProgress = (data: Record<string, unknown>): void => {
    if (typeof data.source_duration_seconds === 'number') {
      sourceDurationSeconds.value = Math.max(0, data.source_duration_seconds)
    }
    if (typeof data.processed_seconds === 'number') {
      processedSeconds.value = Math.max(0, data.processed_seconds)
    }
    if (typeof data.discovered_candidate_count === 'number') {
      discoveredCandidateCount.value = Math.max(0, Math.trunc(data.discovered_candidate_count))
    }
    if (
      data.coverage_status === null
      || data.coverage_status === 'complete'
      || data.coverage_status === 'partial'
      || data.coverage_status === 'insufficient'
    ) {
      coverageStatus.value = data.coverage_status
    }
    if (Array.isArray(data.coverage_gaps)) {
      coverageGaps.value = data.coverage_gaps as CoverageGap[]
    }
  }

  const applyRun = (run: AnalysisRun): void => {
    status.value = run.status
    stage.value = run.stage
    candidates.value = [...run.candidates].sort((left, right) => (
      left.segment.start_seconds - right.segment.start_seconds
    ))
    warnings.value = run.warnings
    error.value = run.error
    emptyReason.value = run.empty_reason
    sourceDurationSeconds.value = run.source_duration_seconds
    processedSeconds.value = run.processed_seconds
    sourceCoveredSeconds.value = run.processed_seconds
    discoveredCandidateCount.value = run.discovered_candidate_count
    coverageStatus.value = run.coverage_status
    coverageGaps.value = run.coverage_gaps
    failureKind.value = run.status === 'failed' ? 'system' : null
    retryAfterSeconds.value = null
  }

  const mergeRetry = (run: AnalysisRun, gap: CoverageGap): void => {
    if (run.status !== 'completed' || run.coverage_status !== 'complete') return
    const runScopedCandidates = run.candidates.map((candidate) => ({
      ...candidate,
      id: `${run.id}:${candidate.id}`,
    }))
    candidates.value = mergeCandidates(candidates.value, runScopedCandidates)
    warnings.value = mergeWarnings(warnings.value, run.warnings)
    coverageGaps.value = coverageGaps.value.filter((current) => !sameGap(current, gap))
    coverageStatus.value = coverageGaps.value.length ? 'partial' : 'complete'
    sourceCoveredSeconds.value = Math.min(
      sourceDurationSeconds.value,
      sourceCoveredSeconds.value + run.processed_seconds,
    )
    processedSeconds.value = sourceCoveredSeconds.value
    discoveredCandidateCount.value = candidates.value.length
    emptyReason.value = candidates.value.length ? null : emptyReason.value
  }

  const restoreSourceProgress = (): void => {
    processedSeconds.value = sourceCoveredSeconds.value
    discoveredCandidateCount.value = candidates.value.length
  }

  const acceptSequence = (runId: string, sequence?: number): boolean => {
    if (!checkpoint || sequence === undefined || !Number.isFinite(sequence)) return true
    const previous = checkpoint.lastSequences[runId] ?? -1
    if (sequence <= previous) return false
    checkpoint.lastSequences[runId] = sequence
    persistCheckpoint()
    return true
  }

  const expireCheckpoint = (): void => {
    closeStream()
    activeRunId.value = null
    activeRunStatus.value = null
    checkpoint = null
    hasOwnedRun.value = false
    checkpointStore.clear()
    restoreError.value = '这次分析已过期，可以重新分析视频'
    if (!candidates.value.length) {
      status.value = 'failed'
      stage.value = 'failed'
      error.value = {
        code: 'provider_error',
        message: restoreError.value,
        retryable: true,
      }
    }
  }

  const refreshRun = async (input: {
    runId: string
    expectedGeneration: number
    client: AnalysisClient
    retryGap?: CoverageGap
  }): Promise<void> => {
    try {
      const run = await input.client.getRun(input.runId)
      if (
        generation !== input.expectedGeneration
        || activeRunId.value !== input.runId
      ) return
      if (run.source_id !== currentSourceId.value) {
        expireCheckpoint()
        return
      }

      activeRunStatus.value = run.status
      transferStatus.value = 'idle'
      if (input.retryGap) {
        stage.value = run.stage
        sourceDurationSeconds.value = run.source_duration_seconds
        processedSeconds.value = run.processed_seconds
        discoveredCandidateCount.value = run.discovered_candidate_count
        if (run.status === 'completed' && run.coverage_status === 'complete') {
          mergeRetry(run, input.retryGap)
          gapRetryError.value = null
        } else if (!isActiveStatus(run.status)) {
          restoreSourceProgress()
          gapRetryError.value = '这段暂时没有重试成功，缺口已保留'
        }
      } else {
        applyRun(run)
      }

      if (!isActiveStatus(run.status)) {
        closeStream()
        activeRunId.value = null
        activeRunStatus.value = null
        gapRetrying.value = null
        if (input.retryGap) stage.value = 'completed'
      }
    } catch (caught) {
      if (generation !== input.expectedGeneration || activeRunId.value !== input.runId) return
      if (caught instanceof AnalysisApiError && caught.status === 404) {
        expireCheckpoint()
        return
      }
      restoreError.value = '连接暂时中断，返回页面时会继续恢复'
    }
  }

  const openStream = (input: {
    runId: string
    expectedGeneration: number
    client: AnalysisClient
    events: AnalysisEventStreamFactory
    retryGap?: CoverageGap
  }): void => {
    closeStream()
    stream = input.events.open(
      input.runId,
      (type, data, sequence) => {
        if (
          generation !== input.expectedGeneration
          || activeRunId.value !== input.runId
          || !acceptSequence(input.runId, sequence)
        ) return
        applyProgress(data)
        const nextStage = data.stage
        if (typeof nextStage === 'string' && nextStage in stageCopy) {
          stage.value = nextStage as AnalysisRun['stage']
        }
        if (type === 'run.completed' || type === 'run.failed' || type === 'run.cancelled') {
          void refreshRun({
            runId: input.runId,
            expectedGeneration: input.expectedGeneration,
            client: input.client,
            retryGap: input.retryGap,
          })
        }
      },
      () => {
        if (generation === input.expectedGeneration && activeRunId.value === input.runId) {
          void refreshRun({
            runId: input.runId,
            expectedGeneration: input.expectedGeneration,
            client: input.client,
            retryGap: input.retryGap,
          })
        }
      },
    )
  }

  async function start(input: {
    sourceId: string
    file?: Blob
    client: AnalysisClient
    events: AnalysisEventStreamFactory
    persistence?: AnalysisCheckpointStore
  }): Promise<void> {
    usePersistence(input.persistence)
    if (isRunning.value) await cancel(input.client)
    else clearResult()

    const currentGeneration = ++generation
    resetResult()
    currentSourceId.value = input.sourceId
    currentSourceKind.value = input.file ? 'local' : 'controlled'
    status.value = 'queued'
    stage.value = 'queued'
    transferStatus.value = input.file ? 'uploading' : 'idle'

    try {
      const created = input.file
        ? await input.client.createLocalRun({
            file: input.file,
            localSourceId: input.sourceId,
          })
        : await input.client.createRun(input.sourceId)
      if (
        generation !== currentGeneration
        || currentSourceId.value !== input.sourceId
        || created.source_id !== input.sourceId
      ) {
        await input.client.cancelRun(created.id).catch(() => undefined)
        if (generation === currentGeneration && currentSourceId.value === input.sourceId) {
          throw new Error('analysis source identity mismatch')
        }
        return
      }
      checkpoint = {
        version: 1,
        sourceId: input.sourceId,
        sourceKind: input.file ? 'local' : 'controlled',
        rootRunId: created.id,
        retries: [],
        lastSequences: {},
      }
      hasOwnedRun.value = true
      persistCheckpoint()
      transferStatus.value = 'idle'
      activeRunId.value = isActiveStatus(created.status) ? created.id : null
      activeRunStatus.value = created.status
      applyRun(created)
      if (isActiveStatus(created.status)) {
        openStream({
          runId: created.id,
          expectedGeneration: currentGeneration,
          client: input.client,
          events: input.events,
        })
      }
    } catch (caught) {
      if (generation !== currentGeneration) return
      transferStatus.value = 'idle'
      activeRunId.value = null
      activeRunStatus.value = null
      status.value = 'failed'
      stage.value = 'failed'
      const capacityError = caught instanceof AnalysisApiError && caught.status === 429
      const localGateError = caught instanceof AnalysisApiError
        && input.file !== undefined
        && [404, 413, 415, 422].includes(caught.status)
      if (localGateError) void loadCapabilities(input.client)
      failureKind.value = capacityError ? 'capacity' : localGateError ? 'local_gate' : 'system'
      retryAfterSeconds.value = capacityError ? caught.retryAfterSeconds : null
      error.value = {
        code: 'provider_error',
        message: capacityError
          ? '真实动作分析名额正在使用中'
          : localGateError
            ? caught.message
            : '动作分析暂时不可用，请重试',
        retryable: true,
      }
    }
  }

  async function retryGap(input: {
    gap: CoverageGap
    file: Blob
    client: AnalysisClient
    events: AnalysisEventStreamFactory
    persistence?: AnalysisCheckpointStore
  }): Promise<void> {
    usePersistence(input.persistence)
    if (!checkpoint || checkpoint.sourceKind !== 'local' || isRunning.value) return
    const sourceId = checkpoint.sourceId
    const currentGeneration = ++generation
    gapRetrying.value = input.gap
    gapRetryError.value = null
    transferStatus.value = 'uploading'
    processedSeconds.value = 0
    discoveredCandidateCount.value = 0
    try {
      const created = await input.client.createLocalRun({
        file: input.file,
        localSourceId: sourceId,
        range: input.gap,
      })
      const identityMismatch = created.source_id !== sourceId
      if (
        generation !== currentGeneration
        || currentSourceId.value !== sourceId
        || identityMismatch
        || !checkpoint
      ) {
        await input.client.cancelRun(created.id).catch(() => undefined)
        if (
          identityMismatch
          && generation === currentGeneration
          && currentSourceId.value === sourceId
          && checkpoint
        ) {
          throw new Error('analysis source identity mismatch')
        }
        return
      }
      checkpoint.retries.push({ runId: created.id, gap: input.gap })
      persistCheckpoint()
      transferStatus.value = 'idle'
      activeRunId.value = isActiveStatus(created.status) ? created.id : null
      activeRunStatus.value = created.status
      stage.value = created.stage
      sourceDurationSeconds.value = created.source_duration_seconds
      processedSeconds.value = created.processed_seconds
      discoveredCandidateCount.value = created.discovered_candidate_count
      if (isActiveStatus(created.status)) {
        openStream({
          runId: created.id,
          expectedGeneration: currentGeneration,
          client: input.client,
          events: input.events,
          retryGap: input.gap,
        })
      } else if (created.status === 'completed' && created.coverage_status === 'complete') {
        mergeRetry(created, input.gap)
        gapRetrying.value = null
      } else {
        gapRetrying.value = null
        restoreSourceProgress()
        gapRetryError.value = '这段暂时没有重试成功，缺口已保留'
        stage.value = 'completed'
      }
    } catch {
      if (generation !== currentGeneration) return
      transferStatus.value = 'idle'
      activeRunId.value = null
      activeRunStatus.value = null
      gapRetrying.value = null
      restoreSourceProgress()
      gapRetryError.value = '这段暂时没有重试成功，缺口已保留'
    }
  }

  async function restore(input: {
    client: AnalysisClient
    events: AnalysisEventStreamFactory
    persistence?: AnalysisCheckpointStore
  }): Promise<void> {
    const persistence = usePersistence(input.persistence)
    const saved = persistence.load()
    if (!saved) return
    checkpoint = saved
    hasOwnedRun.value = true
    const currentGeneration = ++generation
    closeStream()
    restoring.value = true
    restoreError.value = null
    currentSourceId.value = saved.sourceId
    currentSourceKind.value = saved.sourceKind
    try {
      const root = await input.client.getRun(saved.rootRunId)
      if (generation !== currentGeneration) return
      if (root.source_id !== saved.sourceId) {
        expireCheckpoint()
        return
      }
      applyRun(root)
      let active: { run: AnalysisRun; gap?: CoverageGap } | null = isActiveStatus(root.status)
        ? { run: root }
        : null

      for (const retry of saved.retries) {
        let run: AnalysisRun
        try {
          run = await input.client.getRun(retry.runId)
        } catch (caught) {
          if (caught instanceof AnalysisApiError && caught.status === 404) continue
          throw caught
        }
        if (generation !== currentGeneration) return
        if (run.source_id !== saved.sourceId) {
          expireCheckpoint()
          return
        }
        if (run.status === 'completed' && run.coverage_status === 'complete') {
          mergeRetry(run, retry.gap)
        } else if (isActiveStatus(run.status)) {
          active = { run, gap: retry.gap }
        } else if (run.status === 'failed' || run.status === 'cancelled') {
          gapRetryError.value = '这段暂时没有重试成功，缺口已保留'
        }
      }

      if (active) {
        activeRunId.value = active.run.id
        activeRunStatus.value = active.run.status
        stage.value = active.run.stage
        if (active.gap) {
          gapRetrying.value = active.gap
          processedSeconds.value = active.run.processed_seconds
          discoveredCandidateCount.value = active.run.discovered_candidate_count
        }
        openStream({
          runId: active.run.id,
          expectedGeneration: currentGeneration,
          client: input.client,
          events: input.events,
          retryGap: active.gap,
        })
      } else {
        activeRunId.value = null
        activeRunStatus.value = null
        gapRetrying.value = null
      }
    } catch (caught) {
      if (generation !== currentGeneration) return
      if (caught instanceof AnalysisApiError && caught.status === 404) {
        expireCheckpoint()
      } else {
        restoreError.value = '暂时无法恢复这次分析，请稍后返回重试'
      }
    } finally {
      if (generation === currentGeneration) restoring.value = false
    }
  }

  async function cancel(client: AnalysisClient): Promise<void> {
    const runId = activeRunId.value
    const cancellingGap = gapRetrying.value
    generation += 1
    closeStream()
    transferStatus.value = 'idle'
    activeRunId.value = null
    activeRunStatus.value = null

    try {
      if (runId) await client.cancelRun(runId)
    } finally {
      if (cancellingGap && checkpoint) {
        checkpoint.retries = checkpoint.retries.filter((retry) => retry.runId !== runId)
        persistCheckpoint()
        gapRetrying.value = null
        gapRetryError.value = null
        restoreSourceProgress()
        stage.value = 'completed'
      } else {
        checkpoint = null
        hasOwnedRun.value = false
        checkpointStore.clear()
        resetResult()
        currentSourceId.value = null
        currentSourceKind.value = null
        status.value = 'cancelled'
        stage.value = 'cancelled'
      }
    }
  }

  function disconnect(): void {
    closeStream()
  }

  function clearResult(): void {
    generation += 1
    closeStream()
    checkpoint = null
    hasOwnedRun.value = false
    checkpointStore.clear()
    transferStatus.value = 'idle'
    activeRunId.value = null
    activeRunStatus.value = null
    currentSourceId.value = null
    currentSourceKind.value = null
    status.value = 'idle'
    stage.value = 'queued'
    resetResult()
  }

  function clearLocalCheckpoint(): void {
    checkpoint = null
    hasOwnedRun.value = false
    checkpointStore.clear()
    disconnect()
  }

  return {
    sources,
    sourcesLoading,
    sourcesError,
    capabilities,
    capabilitiesLoading,
    capabilitiesError,
    status,
    stage,
    stageLabel,
    transferStatus,
    candidates,
    warnings,
    error,
    emptyReason,
    activeRunId,
    currentSourceId,
    currentSourceKind,
    sourceDurationSeconds,
    processedSeconds,
    discoveredCandidateCount,
    coverageStatus,
    coverageGaps,
    failureKind,
    retryAfterSeconds,
    restoring,
    restoreError,
    gapRetrying,
    gapRetryError,
    isRunning,
    hasOwnedRun,
    loadCapabilities,
    loadSources,
    start,
    retryGap,
    restore,
    cancel,
    disconnect,
    clearResult,
    clearLocalCheckpoint,
  }
})
