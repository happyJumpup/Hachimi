import 'fake-indexeddb/auto'

import Dexie from 'dexie'
import { afterEach, describe, expect, it } from 'vitest'

import { createHachimiDatabase } from '@/db/hachimi-database'
import { createDexieGymtiRepository } from '@/db/gymti-repository'
import type {
  CompletedGymtiResult,
  GymtiAttempt,
  GymtiNarrativeSnapshot,
  PendingGymtiResult,
} from '@/domain/gymti'

const databases: string[] = []

const setup = () => {
  const name = `hachimi-fitness-gymti-${crypto.randomUUID()}`
  databases.push(name)
  const database = createHachimiDatabase(name)
  const repository = createDexieGymtiRepository(database, {
    now: () => new Date('2026-07-23T00:10:00.000Z'),
  })
  return { database, repository }
}

const result: CompletedGymtiResult = {
  gymtiType: 'LIFE',
  secondaryGymtiType: 'CURV',
  recommendedCoachStyleId: 'gentle',
  reasonCodes: ['steady_progress', 'supportive_feedback'],
  excludedCoachStyleIds: ['snarky'],
}

const attempt = (): GymtiAttempt => ({
  id: 'current',
  attemptId: 'attempt-new',
  questionnaireVersion: 'questionnaire.v1',
  scoringVersion: 'scoring.v1',
  answers: [{ questionId: 'q1', optionId: 'q1-a' }],
  currentQuestionId: 'q2',
  phase: 'questionnaire',
  originPath: '/plan',
  startedAt: '2026-07-23T00:00:00.000Z',
  updatedAt: '2026-07-23T00:00:00.000Z',
})

const pending = (): PendingGymtiResult => ({
  id: 'current',
  resultId: 'result-new',
  attemptId: 'attempt-new',
  questionnaireVersion: 'questionnaire.v1',
  scoringVersion: 'scoring.v1',
  answers: [{ questionId: 'q1', optionId: 'q1-a' }],
  result,
  narrative: null,
  createdAt: '2026-07-23T00:05:00.000Z',
  updatedAt: '2026-07-23T00:05:00.000Z',
})

const narrative: GymtiNarrativeSnapshot = {
  text: '你更适合把训练放进稳定的生活节奏里。',
  source: 'template',
  version: 'narrative.v1',
  model: null,
  generatedAt: '2026-07-23T00:08:00.000Z',
}

afterEach(async () => {
  await Promise.all(databases.splice(0).map((name) => Dexie.delete(name)))
})

describe('GYMTI result lifecycle repository', () => {
  it('keeps the stable current result while a retest is unfinished', async () => {
    const { database, repository } = setup()
    await database.gymtiResults.put({
      ...pending(),
      resultId: 'result-old',
      attemptId: 'attempt-old',
      narrative,
      activatedAt: '2026-07-22T00:00:00.000Z',
    })

    await repository.saveAttempt(attempt())
    await repository.savePendingResult(pending())

    expect((await repository.loadState()).current?.resultId).toBe('result-old')
    expect((await repository.loadState()).pending?.resultId).toBe('result-new')
    database.close()
  })

  it('stores a narrative once and activates a pending result atomically', async () => {
    const { database, repository } = setup()
    await repository.saveAttempt(attempt())
    await repository.savePendingResult(pending())

    await repository.attachNarrative('result-new', narrative)
    await expect(repository.attachNarrative('result-new', {
      ...narrative,
      text: '不应覆盖已经保存的叙事',
    })).rejects.toThrow(/already has a narrative/)

    const activated = await repository.activatePendingResult('result-new')
    expect(activated).toMatchObject({
      resultId: 'result-new',
      narrative,
      activatedAt: '2026-07-23T00:10:00.000Z',
    })
    const state = await repository.loadState()
    expect(state.attempt).toBeNull()
    expect(state.pending).toBeNull()
    expect(state.current?.resultId).toBe('result-new')
    database.close()
  })

  it('refuses to activate a result before its display narrative exists', async () => {
    const { database, repository } = setup()
    await repository.savePendingResult(pending())

    await expect(repository.activatePendingResult('result-new')).rejects.toThrow(
      /does not have a narrative/,
    )
    expect((await repository.loadState()).pending?.resultId).toBe('result-new')
    database.close()
  })
})
