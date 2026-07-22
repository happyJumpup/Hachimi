import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { GymtiRepository } from '@/db/gymti-repository'
import type { CompletedGymtiResult, GymtiNarrativeSnapshot } from '@/domain/gymti'
import { useGymtiStore } from '@/stores/gymti'

const formalResult: CompletedGymtiResult = {
  gymtiType: 'LIFE',
  secondaryGymtiType: null,
  recommendedCoachStyleId: 'gentle',
  reasonCodes: ['steady_progress'],
  excludedCoachStyleIds: [],
}

const narrative: GymtiNarrativeSnapshot = {
  text: '稳定地练下去，比追逐一次极限更适合你。',
  source: 'template',
  version: 'narrative.v1',
  model: null,
  generatedAt: '2026-07-23T00:08:00.000Z',
}

const repository = (): GymtiRepository => ({
  loadState: vi.fn().mockResolvedValue({ attempt: null, pending: null, current: null }),
  startAttempt: vi.fn().mockResolvedValue(undefined),
  saveAttempt: vi.fn().mockResolvedValue(undefined),
  discardAttempt: vi.fn().mockResolvedValue(undefined),
  saveCompletedAttempt: vi.fn().mockResolvedValue(undefined),
  reopenAttempt: vi.fn().mockResolvedValue(undefined),
  savePendingResult: vi.fn().mockResolvedValue(undefined),
  attachNarrative: vi.fn().mockImplementation(async (_resultId, snapshot) => ({
    ...storePending,
    narrative: snapshot,
  })),
  activatePendingResult: vi.fn().mockImplementation(async () => ({
    ...storePending,
    narrative,
    activatedAt: '2026-07-23T00:10:00.000Z',
  })),
})

let storePending: Record<string, unknown>

beforeEach(() => {
  setActivePinia(createPinia())
  storePending = {}
})

describe('GYMTI lifecycle store', () => {
  it('commits the selected answer and pending result in one repository operation', async () => {
    const persistence = repository()
    const store = useGymtiStore()
    await store.load(persistence)
    await store.beginAttempt({
      questionnaireVersion: 'questionnaire.v1',
      scoringVersion: 'scoring.v1',
      firstQuestionId: 'q1',
      originPath: '/plan',
    })
    const completedAnswers = [{ questionId: 'q1', optionId: 'q1-a' }]

    await store.completeAttempt(formalResult, completedAnswers)

    expect(persistence.saveAttempt).not.toHaveBeenCalled()
    expect(persistence.saveCompletedAttempt).toHaveBeenCalledWith(
      expect.objectContaining({
        answers: completedAnswers,
        phase: 'profile',
      }),
      expect.objectContaining({
        answers: completedAnswers,
        result: formalResult,
      }),
    )
    expect(store.attempt?.answers).toEqual(completedAnswers)
    expect(store.pending?.answers).toEqual(completedAnswers)
  })

  it('persists questionnaire progress and creates a pending result without replacing current', async () => {
    const persistence = repository()
    vi.mocked(persistence.saveCompletedAttempt).mockImplementation(async (attempt, result) => {
      structuredClone(attempt)
      structuredClone(result)
    })
    vi.mocked(persistence.reopenAttempt).mockImplementation(async (attempt) => {
      structuredClone(attempt)
    })
    const store = useGymtiStore()
    await store.load(persistence)
    await store.beginAttempt({
      questionnaireVersion: 'questionnaire.v1',
      scoringVersion: 'scoring.v1',
      firstQuestionId: 'q1',
      originPath: '/plan',
    })
    await store.updateProgress({
      answers: [{ questionId: 'q1', optionId: 'q1-a' }],
      currentQuestionId: 'q2',
    })

    expect(persistence.saveAttempt).toHaveBeenLastCalledWith(expect.objectContaining({
      answers: [{ questionId: 'q1', optionId: 'q1-a' }],
      currentQuestionId: 'q2',
    }))

    await store.completeAttempt(formalResult)
    expect(persistence.saveCompletedAttempt).toHaveBeenCalledWith(
      expect.objectContaining({ phase: 'profile' }),
      expect.objectContaining({ result: formalResult, narrative: null }),
    )
    expect(store.current).toBeNull()
    expect(store.pending?.result).toEqual(formalResult)

    await store.reopenQuestionnaire('q1')
    expect(persistence.reopenAttempt).toHaveBeenCalledWith(expect.objectContaining({
      currentQuestionId: 'q1',
      phase: 'questionnaire',
    }))
    expect(store.pending).toBeNull()
  })

  it('generates one narrative snapshot and activates it only when presented', async () => {
    const persistence = repository()
    const store = useGymtiStore()
    await store.load(persistence)
    await store.beginAttempt({
      questionnaireVersion: 'questionnaire.v1',
      scoringVersion: 'scoring.v1',
      firstQuestionId: 'q1',
      originPath: '/mine',
    })
    await store.updateProgress({
      answers: [{ questionId: 'q1', optionId: 'q1-a' }],
      currentQuestionId: 'q1',
    })
    await store.completeAttempt(formalResult)
    storePending = store.pending as unknown as Record<string, unknown>
    const generate = vi.fn().mockResolvedValue(narrative)

    await store.ensureNarrative(generate)
    await store.ensureNarrative(generate)
    expect(generate).toHaveBeenCalledTimes(1)
    expect(persistence.attachNarrative).toHaveBeenCalledTimes(1)
    expect(store.current).toBeNull()

    await store.presentPendingResult()
    expect(persistence.activatePendingResult).toHaveBeenCalledTimes(1)
    expect(store.pending).toBeNull()
    expect(store.attempt).toBeNull()
    expect(store.current?.resultId).toBeTruthy()
  })
})
