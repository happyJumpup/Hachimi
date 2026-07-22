import { ref } from 'vue'
import { defineStore } from 'pinia'

import type { GymtiRepository } from '@/db/gymti-repository'
import type {
  CompletedGymtiResult,
  CurrentGymtiResult,
  GymtiAnswerRef,
  GymtiAttempt,
  GymtiNarrativeSnapshot,
  PendingGymtiResult,
} from '@/domain/gymti'

interface BeginAttemptInput {
  questionnaireVersion: string
  scoringVersion: string
  firstQuestionId: string
  originPath: string
}

interface ProgressInput {
  answers: GymtiAnswerRef[]
  currentQuestionId: string
}

type NarrativeFactory = (pending: PendingGymtiResult) => Promise<GymtiNarrativeSnapshot>

const cloneJson = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T

export const useGymtiStore = defineStore('gymti', () => {
  const attempt = ref<GymtiAttempt | null>(null)
  const pending = ref<PendingGymtiResult | null>(null)
  const current = ref<CurrentGymtiResult | null>(null)
  const loaded = ref(false)
  const persistenceSuspended = ref(false)
  let repository: GymtiRepository | null = null
  let narrativeOperation: Promise<GymtiNarrativeSnapshot> | null = null
  const operations = new Set<Promise<unknown>>()

  const requireRepository = (): GymtiRepository => {
    if (!repository) throw new Error('GYMTI lifecycle is not loaded')
    return repository
  }

  const runOperation = <T>(operation: () => Promise<T>): Promise<T> => {
    if (persistenceSuspended.value) {
      return Promise.reject(new Error('local data persistence is suspended'))
    }
    const running = operation()
    operations.add(running)
    void running.then(
      () => operations.delete(running),
      () => operations.delete(running),
    )
    return running
  }

  async function load(nextRepository: GymtiRepository): Promise<void> {
    repository = nextRepository
    const state = await nextRepository.loadState()
    attempt.value = state.attempt
    pending.value = state.pending
    current.value = state.current
    loaded.value = true
  }

  async function reload(): Promise<void> {
    const state = await requireRepository().loadState()
    attempt.value = state.attempt
    pending.value = state.pending
    current.value = state.current
  }

  async function beginAttempt(input: BeginAttemptInput): Promise<GymtiAttempt> {
    const timestamp = new Date().toISOString()
    const next: GymtiAttempt = {
      id: 'current',
      attemptId: crypto.randomUUID(),
      questionnaireVersion: input.questionnaireVersion,
      scoringVersion: input.scoringVersion,
      answers: [],
      currentQuestionId: input.firstQuestionId,
      phase: 'questionnaire',
      originPath: input.originPath,
      startedAt: timestamp,
      updatedAt: timestamp,
    }
    await runOperation(() => requireRepository().startAttempt(next))
    attempt.value = next
    pending.value = null
    narrativeOperation = null
    return next
  }

  async function updateProgress(input: ProgressInput): Promise<GymtiAttempt> {
    if (!attempt.value) throw new Error('GYMTI attempt does not exist')
    const next: GymtiAttempt = {
      ...attempt.value,
      answers: cloneJson(input.answers),
      currentQuestionId: input.currentQuestionId,
      phase: 'questionnaire',
      updatedAt: new Date().toISOString(),
    }
    await runOperation(() => requireRepository().saveAttempt(next))
    attempt.value = next
    return next
  }

  async function completeAttempt(
    result: CompletedGymtiResult,
    answers: GymtiAnswerRef[] = attempt.value?.answers ?? [],
  ): Promise<PendingGymtiResult> {
    if (!attempt.value || answers.length === 0) {
      throw new Error('GYMTI attempt cannot complete without answers')
    }
    const timestamp = new Date().toISOString()
    const completedAttempt: GymtiAttempt = {
      ...cloneJson(attempt.value),
      answers: cloneJson(answers),
      phase: 'profile',
      updatedAt: timestamp,
    }
    const next: PendingGymtiResult = {
      id: 'current',
      resultId: crypto.randomUUID(),
      attemptId: completedAttempt.attemptId,
      questionnaireVersion: completedAttempt.questionnaireVersion,
      scoringVersion: completedAttempt.scoringVersion,
      answers: cloneJson(completedAttempt.answers),
      result: cloneJson(result),
      narrative: null,
      createdAt: timestamp,
      updatedAt: timestamp,
    }
    await runOperation(() => requireRepository().saveCompletedAttempt(completedAttempt, next))
    attempt.value = completedAttempt
    pending.value = next
    narrativeOperation = null
    return next
  }

  async function reopenQuestionnaire(currentQuestionId: string): Promise<GymtiAttempt> {
    if (!attempt.value || attempt.value.phase !== 'profile') {
      throw new Error('completed GYMTI attempt does not exist')
    }
    const reopened: GymtiAttempt = {
      ...cloneJson(attempt.value),
      currentQuestionId,
      phase: 'questionnaire',
      updatedAt: new Date().toISOString(),
    }
    await runOperation(() => requireRepository().reopenAttempt(cloneJson(reopened)))
    attempt.value = reopened
    pending.value = null
    narrativeOperation = null
    return reopened
  }

  async function ensureNarrative(factory: NarrativeFactory): Promise<GymtiNarrativeSnapshot> {
    if (!pending.value) throw new Error('pending GYMTI result does not exist')
    if (pending.value.narrative) return pending.value.narrative
    if (narrativeOperation) return narrativeOperation

    const resultId = pending.value.resultId
    const input = cloneJson(pending.value)
    narrativeOperation = runOperation(async () => {
      const snapshot = await factory(input)
      if (!pending.value || pending.value.resultId !== resultId) {
        throw new Error('pending GYMTI result changed before narrative completed')
      }
      const saved = await requireRepository().attachNarrative(resultId, snapshot)
      pending.value = saved
      return snapshot
    })

    try {
      return await narrativeOperation
    } finally {
      narrativeOperation = null
    }
  }

  async function presentPendingResult(): Promise<CurrentGymtiResult> {
    if (!pending.value?.narrative) {
      throw new Error('pending GYMTI result is not ready to present')
    }
    const activated = await runOperation(() =>
      requireRepository().activatePendingResult(pending.value!.resultId))
    current.value = activated
    pending.value = null
    attempt.value = null
    narrativeOperation = null
    return activated
  }

  async function quiescePersistence(): Promise<void> {
    persistenceSuspended.value = true
    await Promise.allSettled([...operations])
  }

  function resumePersistence(): void {
    persistenceSuspended.value = false
  }

  function resetLocalState(keepSuspended = false): void {
    attempt.value = null
    pending.value = null
    current.value = null
    narrativeOperation = null
    persistenceSuspended.value = keepSuspended
  }

  return {
    attempt,
    pending,
    current,
    loaded,
    persistenceSuspended,
    load,
    reload,
    beginAttempt,
    updateProgress,
    completeAttempt,
    reopenQuestionnaire,
    ensureNarrative,
    presentPendingResult,
    quiescePersistence,
    resumePersistence,
    resetLocalState,
  }
})
