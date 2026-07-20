import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import type { DraftPlan } from '@/domain/types'
import type {
  PlanSnapshot,
  TrainingRecord,
  TrainingSession,
} from '@/domain/training'
import { QUICK_EXPERIENCE_PLAN_NAME } from '@/features/quick-experience/fixture'
import type {
  PlanValidationIssue,
  TrainingEngine,
  TrainingEngineErrorCode,
  TrainingEngineResult,
} from '@/training/training-engine'

const cloneJson = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T

export const useTrainingStore = defineStore('training', () => {
  const session = ref<TrainingSession | null>(null)
  const lastRecord = ref<TrainingRecord | null>(null)
  const errorCode = ref<TrainingEngineErrorCode | null>(null)
  const errorMessage = ref<string | null>(null)
  const validationIssues = ref<PlanValidationIssue[]>([])
  const loaded = ref(false)
  let engine: TrainingEngine | null = null
  let commandQueue: Promise<void> = Promise.resolve()

  const serialize = <T>(operation: () => Promise<T>): Promise<T> => {
    const result = commandQueue.then(operation, operation)
    commandQueue = result.then(() => undefined, () => undefined)
    return result
  }

  const hasCurrent = computed(() => session.value !== null)
  const currentItem = computed(() =>
    session.value?.plan.items[session.value.currentItemIndex] ?? null,
  )
  const currentProgress = computed(() =>
    session.value?.progress[session.value.currentItemIndex] ?? null,
  )

  const applyResult = (result: TrainingEngineResult): TrainingEngineResult => {
    if (result.ok) {
      session.value = result.session
      if (result.record) lastRecord.value = result.record
      errorCode.value = null
      errorMessage.value = null
      validationIssues.value = []
      return result
    }

    if (result.session) session.value = result.session
    errorCode.value = result.code
    errorMessage.value = result.message
    validationIssues.value = result.issues ?? []
    return result
  }

  const unavailable = (): TrainingEngineResult => ({
    ok: false,
    code: 'storage_unavailable',
    message: '本机训练数据暂时无法读取',
    session: session.value,
  })

  async function load(nextEngine: TrainingEngine): Promise<TrainingEngineResult> {
    engine = nextEngine
    const result = applyResult(await engine.restore())
    loaded.value = true
    return result
  }

  async function restore(): Promise<TrainingEngineResult> {
    return serialize(async () => {
      if (!engine) return applyResult(unavailable())
      return applyResult(await engine.restore())
    })
  }

  async function createFromDraft(draft: DraftPlan): Promise<TrainingEngineResult> {
    return serialize(async () => {
      if (!engine) return applyResult(unavailable())
      const snapshot: PlanSnapshot = {
        name: draft.name,
        source: draft.linkedPlanId
          ? 'saved'
          : draft.name === QUICK_EXPERIENCE_PLAN_NAME ? 'sample' : 'draft',
        sourcePlanId: draft.linkedPlanId,
        items: cloneJson(draft.items),
      }
      return applyResult(await engine.dispatch({ type: 'session.create', plan: snapshot }))
    })
  }

  async function runVersioned(
    command: (
      current: TrainingSession,
    ) => Parameters<TrainingEngine['dispatch']>[0],
  ): Promise<TrainingEngineResult> {
    return serialize(async () => {
      if (!engine || !session.value) {
        return applyResult({
          ok: false,
          code: 'no_active_session',
          message: '没有可继续的训练',
          session: null,
        })
      }
      return applyResult(await engine.dispatch(command(session.value)))
    })
  }

  const startSet = () => runVersioned((current) => ({
    type: 'set.start',
    sessionId: current.sessionId,
    expectedRevision: current.revision,
  }))

  const tick = () => runVersioned((current) => ({
    type: 'clock.tick',
    sessionId: current.sessionId,
    expectedRevision: current.revision,
  }))

  const pause = (reason: 'user' | 'page_hidden') => runVersioned((current) => ({
    type: 'session.pause',
    reason,
    sessionId: current.sessionId,
    expectedRevision: current.revision,
  }))

  const completeSet = () => runVersioned((current) => ({
    type: 'set.complete',
    sessionId: current.sessionId,
    expectedRevision: current.revision,
  }))

  const continueRest = () => runVersioned((current) => ({
    type: 'rest.continue',
    sessionId: current.sessionId,
    expectedRevision: current.revision,
  }))

  const skipAction = () => runVersioned((current) => ({
    type: 'action.skip',
    sessionId: current.sessionId,
    expectedRevision: current.revision,
  }))

  const endEarly = () => runVersioned((current) => ({
    type: 'session.end_early',
    sessionId: current.sessionId,
    expectedRevision: current.revision,
  }))

  function resetLocalState(): void {
    commandQueue = Promise.resolve()
    session.value = null
    lastRecord.value = null
    errorCode.value = null
    errorMessage.value = null
    validationIssues.value = []
  }

  return {
    session,
    lastRecord,
    errorCode,
    errorMessage,
    validationIssues,
    loaded,
    hasCurrent,
    currentItem,
    currentProgress,
    load,
    restore,
    createFromDraft,
    startSet,
    tick,
    pause,
    completeSet,
    continueRest,
    skipAction,
    endEarly,
    resetLocalState,
  }
})
