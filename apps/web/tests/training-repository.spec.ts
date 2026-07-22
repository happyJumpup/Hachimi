import 'fake-indexeddb/auto'

import Dexie from 'dexie'
import { afterEach, describe, expect, it } from 'vitest'

import { createHachimiDatabase } from '@/db/hachimi-database'
import { createDexieTrainingRepository } from '@/db/training-repository'
import type { TrainingRecord, TrainingSession } from '@/domain/training'

const databases: string[] = []

const createRepository = () => {
  const name = `hachimi-fitness-training-${crypto.randomUUID()}`
  databases.push(name)
  const database = createHachimiDatabase(name)
  return { database, repository: createDexieTrainingRepository(database) }
}

const session = (sessionId: string, revision = 0): TrainingSession => ({
  id: 'current',
  sessionId,
  revision,
  status: 'paused',
  pauseReason: 'before_start',
  plan: { name: '测试方案', source: 'draft', sourcePlanId: null, items: [] },
  currentItemIndex: 0,
  currentSetIndex: 0,
  currentSetActiveMilliseconds: 0,
  activeStartedAt: null,
  restStartedAt: null,
  restEndsAt: null,
  scheduledRestSeconds: null,
  creditedRestMilliseconds: 0,
  progress: [],
  petId: 'hachimi',
  coachStyleId: null,
  startedAt: '2026-07-21T00:00:00.000Z',
  updatedAt: '2026-07-21T00:00:00.000Z',
})

const record = (sessionId: string): TrainingRecord => ({
  id: sessionId,
  outcome: 'completed',
  plan: { name: '测试方案', source: 'draft', sourcePlanId: null, items: [] },
  actions: [],
  activeSeconds: 0,
  creditedRestSeconds: 0,
  trainingDurationSeconds: 0,
  completedActionCount: 0,
  calorie: { value: 12, method: 'generic' },
  petId: 'hachimi',
  coachStyleId: null,
  startedAt: '2026-07-21T00:00:00.000Z',
  endedAt: '2026-07-21T00:01:00.000Z',
})

afterEach(async () => {
  await Promise.all(databases.splice(0).map((name) => Dexie.delete(name)))
})

describe('IndexedDB training repository', () => {
  it('creates only one current session and rejects a stale revision atomically', async () => {
    const { database, repository } = createRepository()
    const first = session('session-a')
    const second = session('session-b')

    expect(await repository.createCurrent(first)).toEqual({ status: 'created', session: first })
    expect(await repository.createCurrent(second)).toEqual({ status: 'exists', session: first })

    const next = { ...first, revision: 1, status: 'active' as const, pauseReason: null }
    expect(await repository.commit({
      sessionId: first.sessionId,
      expectedRevision: 0,
      nextSession: next,
    })).toEqual({ status: 'committed', session: next, record: null })

    const stale = { ...next, revision: 1, status: 'paused' as const, pauseReason: 'user' as const }
    expect(await repository.commit({
      sessionId: first.sessionId,
      expectedRevision: 0,
      nextSession: stale,
    })).toEqual({ status: 'conflict', session: next })
    expect(await repository.loadCurrent()).toEqual(next)

    database.close()
  })

  it('writes one terminal record and removes current in the same idempotent transaction', async () => {
    const { database, repository } = createRepository()
    const current = session('session-terminal', 4)
    const terminal = record(current.sessionId)
    await repository.createCurrent(current)

    const first = await repository.commit({
      sessionId: current.sessionId,
      expectedRevision: 4,
      record: terminal,
    })
    expect(first).toEqual({ status: 'committed', session: null, record: terminal })
    expect(await repository.loadCurrent()).toBeNull()
    expect(await repository.loadRecord(current.sessionId)).toEqual(terminal)

    const replay = await repository.commit({
      sessionId: current.sessionId,
      expectedRevision: 4,
      record: terminal,
    })
    expect(replay).toEqual({ status: 'already_finalized', record: terminal })
    expect(await database.records.count()).toBe(1)

    database.close()
  })
})
