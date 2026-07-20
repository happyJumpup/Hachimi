import { describe, expect, it } from 'vitest'

import type {
  CommitResult,
  CreateCurrentResult,
  TrainingCommit,
  TrainingPersistence,
} from '@/db/training-repository'
import type { DraftItem } from '@/domain/types'
import type {
  CalorieEstimate,
  PlanSnapshot,
  TrainingProfile,
  TrainingRecord,
  TrainingSession,
} from '@/domain/training'
import {
  createTrainingEngine,
  type TrainingClock,
  type TrainingEngineResult,
} from '@/training/training-engine'

class MemoryTrainingPersistence implements TrainingPersistence {
  current: TrainingSession | null = null
  records = new Map<string, TrainingRecord>()
  profile: TrainingProfile | null = null

  async loadCurrent(): Promise<TrainingSession | null> {
    return structuredClone(this.current)
  }

  async loadRecord(sessionId: string): Promise<TrainingRecord | null> {
    return structuredClone(this.records.get(sessionId) ?? null)
  }

  async loadProfile(): Promise<TrainingProfile | null> {
    return structuredClone(this.profile)
  }

  async createCurrent(session: TrainingSession): Promise<CreateCurrentResult> {
    if (this.current) return { status: 'exists', session: structuredClone(this.current) }
    this.current = structuredClone(session)
    return { status: 'created', session: structuredClone(session) }
  }

  async commit(change: TrainingCommit): Promise<CommitResult> {
    const finalized = this.records.get(change.sessionId)
    if (finalized) return { status: 'already_finalized', record: structuredClone(finalized) }
    if (!this.current) return { status: 'missing' }
    if (
      this.current.sessionId !== change.sessionId
      || this.current.revision !== change.expectedRevision
    ) {
      return { status: 'conflict', session: structuredClone(this.current) }
    }
    if (change.record !== undefined) {
      this.records.set(change.record.id, structuredClone(change.record))
      this.current = null
      return { status: 'committed', session: null, record: structuredClone(change.record) }
    }
    this.current = structuredClone(change.nextSession)
    return { status: 'committed', session: structuredClone(change.nextSession), record: null }
  }
}

class FakeTrainingClock implements TrainingClock {
  private wallMilliseconds = Date.parse('2026-07-21T00:00:00.000Z')
  private monotonic = 0

  now(): Date {
    return new Date(this.wallMilliseconds)
  }

  monotonicMilliseconds(): number {
    return this.monotonic
  }

  advance(milliseconds: number): void {
    this.wallMilliseconds += milliseconds
    this.monotonic += milliseconds
  }
}

const sourced = <T>(value: T | null, source: 'video' | 'rule' | 'user' | null) => ({
  value,
  source,
})

const repsAction = (input: Partial<DraftItem> = {}): DraftItem => ({
  id: 'curl',
  name: '拖拽弯举',
  sourceRef: null,
  segment: sourced(null, null),
  mode: 'reps',
  sets: sourced(2, 'user'),
  reps: sourced(10, 'user'),
  durationSeconds: sourced(null, null),
  restSeconds: sourced(0, 'user'),
  weightKg: sourced(null, null),
  ...input,
})

const durationAction = (input: Partial<DraftItem> = {}): DraftItem => ({
  id: 'plank',
  name: '平板支撑',
  sourceRef: null,
  segment: sourced(null, null),
  mode: 'duration',
  sets: sourced(1, 'user'),
  reps: sourced(null, null),
  durationSeconds: sourced(2, 'user'),
  restSeconds: sourced(0, 'user'),
  weightKg: sourced(null, null),
  ...input,
})

const plan = (...items: DraftItem[]): PlanSnapshot => ({
  name: '手臂训练',
  source: 'draft',
  sourcePlanId: null,
  items,
})

const expectSuccess = (result: TrainingEngineResult) => {
  expect(result.ok).toBe(true)
  if (!result.ok) throw new Error(`expected success, received ${result.code}`)
  return result
}

const setup = () => {
  const persistence = new MemoryTrainingPersistence()
  const clock = new FakeTrainingClock()
  const estimateCalories = (): CalorieEstimate | null => null
  const engine = createTrainingEngine({
    persistence,
    clock,
    idFactory: () => 'session-1',
    estimateCalories,
  })
  return { clock, engine, persistence }
}

describe('TrainingEngine public command interface', () => {
  it('completes a reps session once and replays the same terminal record idempotently', async () => {
    const { clock, engine, persistence } = setup()
    const sourcePlan = plan(repsAction())

    const created = expectSuccess(await engine.dispatch({
      type: 'session.create',
      plan: sourcePlan,
    }))
    expect(created.session).toMatchObject({
      sessionId: 'session-1',
      revision: 0,
      status: 'paused',
      pauseReason: 'before_start',
    })
    sourcePlan.items[0].name = '已在调用方修改'
    expect(created.session?.plan.items[0].name).toBe('拖拽弯举')

    const firstStarted = expectSuccess(await engine.dispatch({
      type: 'set.start',
      sessionId: 'session-1',
      expectedRevision: 0,
    }))
    expect(firstStarted.events).toEqual([
      { type: 'session.started', sessionId: 'session-1' },
      { type: 'set.started', itemId: 'curl', setIndex: 0 },
    ])

    clock.advance(1_500)
    const firstCompleted = expectSuccess(await engine.dispatch({
      type: 'set.complete',
      sessionId: 'session-1',
      expectedRevision: 1,
    }))
    expect(firstCompleted.session).toMatchObject({
      revision: 2,
      status: 'ready_to_continue',
      currentItemIndex: 0,
      currentSetIndex: 1,
      currentSetActiveMilliseconds: 0,
    })
    expect(firstCompleted.session?.progress[0]).toMatchObject({
      completedSets: 1,
      activeMilliseconds: 1_500,
    })

    expectSuccess(await engine.dispatch({
      type: 'set.start',
      sessionId: 'session-1',
      expectedRevision: 2,
    }))
    clock.advance(500)
    const completed = expectSuccess(await engine.dispatch({
      type: 'set.complete',
      sessionId: 'session-1',
      expectedRevision: 3,
    }))

    expect(completed.session).toBeNull()
    expect(completed.record).toMatchObject({
      id: 'session-1',
      outcome: 'completed',
      activeSeconds: 2,
      creditedRestSeconds: 0,
      trainingDurationSeconds: 2,
      completedActionCount: 1,
      calorie: null,
      actions: [{
        itemId: 'curl',
        completedSets: 2,
        completedReps: 20,
        completedDurationSeconds: null,
        status: 'completed',
      }],
    })
    expect(persistence.records.size).toBe(1)

    const replay = expectSuccess(await engine.dispatch({
      type: 'set.complete',
      sessionId: 'session-1',
      expectedRevision: 3,
    }))
    expect(replay.record).toEqual(completed.record)
    expect(replay.events).toEqual([])
    expect(persistence.records.size).toBe(1)
  })

  it('counts duration only while active in the foreground and auto-completes at the target', async () => {
    const { clock, engine, persistence } = setup()
    expectSuccess(await engine.dispatch({ type: 'session.create', plan: plan(durationAction()) }))
    expectSuccess(await engine.dispatch({
      type: 'set.start',
      sessionId: 'session-1',
      expectedRevision: 0,
    }))

    clock.advance(800)
    const ticked = expectSuccess(await engine.dispatch({
      type: 'clock.tick',
      sessionId: 'session-1',
      expectedRevision: 1,
    }))
    expect(ticked.session?.currentSetActiveMilliseconds).toBe(800)

    clock.advance(200)
    const paused = expectSuccess(await engine.dispatch({
      type: 'session.pause',
      reason: 'page_hidden',
      sessionId: 'session-1',
      expectedRevision: 2,
    }))
    expect(paused.session).toMatchObject({
      revision: 3,
      status: 'paused',
      pauseReason: 'page_hidden',
      currentSetActiveMilliseconds: 1_000,
    })

    clock.advance(30_000)
    const restoredEngine = createTrainingEngine({
      persistence,
      clock,
      idFactory: () => 'unused',
      estimateCalories: () => null,
    })
    const restored = expectSuccess(await restoredEngine.restore())
    expect(restored.session).toMatchObject({
      status: 'paused',
      currentSetActiveMilliseconds: 1_000,
    })

    expectSuccess(await restoredEngine.dispatch({
      type: 'set.start',
      sessionId: 'session-1',
      expectedRevision: 3,
    }))
    clock.advance(1_000)
    const completed = expectSuccess(await restoredEngine.dispatch({
      type: 'clock.tick',
      sessionId: 'session-1',
      expectedRevision: 4,
    }))
    expect(completed.record).toMatchObject({
      outcome: 'completed',
      activeSeconds: 2,
      actions: [{ completedDurationSeconds: 2, status: 'completed' }],
    })
  })

  it('caps an expired wall-clock rest and restores into ready to continue', async () => {
    const { clock, engine, persistence } = setup()
    expectSuccess(await engine.dispatch({
      type: 'session.create',
      plan: plan(repsAction({ restSeconds: sourced(60, 'video') })),
    }))
    expectSuccess(await engine.dispatch({
      type: 'set.start',
      sessionId: 'session-1',
      expectedRevision: 0,
    }))
    const resting = expectSuccess(await engine.dispatch({
      type: 'set.complete',
      sessionId: 'session-1',
      expectedRevision: 1,
    }))
    expect(resting.session).toMatchObject({
      revision: 2,
      status: 'resting',
      restStartedAt: '2026-07-21T00:00:00.000Z',
      restEndsAt: '2026-07-21T00:01:00.000Z',
      scheduledRestSeconds: 60,
    })

    clock.advance(100_000)
    const restoredEngine = createTrainingEngine({ persistence, clock, estimateCalories: () => null })
    const restored = expectSuccess(await restoredEngine.restore())
    expect(restored.session).toMatchObject({
      revision: 3,
      status: 'ready_to_continue',
      creditedRestMilliseconds: 60_000,
      restStartedAt: null,
      restEndsAt: null,
      scheduledRestSeconds: null,
    })
    expect(restored.events).toEqual([{ type: 'rest.finished' }])
  })

  it('normalizes a refreshed active session to paused without adding wall-clock time', async () => {
    const { clock, engine, persistence } = setup()
    expectSuccess(await engine.dispatch({ type: 'session.create', plan: plan(durationAction()) }))
    expectSuccess(await engine.dispatch({
      type: 'set.start',
      sessionId: 'session-1',
      expectedRevision: 0,
    }))

    clock.advance(15_000)
    const refreshedEngine = createTrainingEngine({ persistence, clock, estimateCalories: () => null })
    const restored = expectSuccess(await refreshedEngine.restore())
    expect(restored.session).toMatchObject({
      revision: 2,
      status: 'paused',
      pauseReason: 'recovered',
      activeStartedAt: null,
      currentSetActiveMilliseconds: 0,
    })
    expect(restored.events).toEqual([{ type: 'session.paused', reason: 'recovered' }])
  })

  it('credits only elapsed rest when the user continues early', async () => {
    const { clock, engine } = setup()
    expectSuccess(await engine.dispatch({
      type: 'session.create',
      plan: plan(repsAction({ restSeconds: sourced(60, 'video') })),
    }))
    expectSuccess(await engine.dispatch({
      type: 'set.start',
      sessionId: 'session-1',
      expectedRevision: 0,
    }))
    expectSuccess(await engine.dispatch({
      type: 'set.complete',
      sessionId: 'session-1',
      expectedRevision: 1,
    }))

    clock.advance(20_000)
    const continued = expectSuccess(await engine.dispatch({
      type: 'rest.continue',
      sessionId: 'session-1',
      expectedRevision: 2,
    }))
    expect(continued.session).toMatchObject({
      revision: 3,
      status: 'active',
      creditedRestMilliseconds: 20_000,
      restStartedAt: null,
      restEndsAt: null,
    })
    expect(continued.events).toEqual([
      { type: 'rest.finished' },
      { type: 'set.started', itemId: 'curl', setIndex: 1 },
    ])
  })

  it('preserves partial work when skipping an action and ending the session early', async () => {
    const { clock, engine } = setup()
    expectSuccess(await engine.dispatch({
      type: 'session.create',
      plan: plan(
        repsAction({ sets: sourced(3, 'user') }),
        durationAction(),
      ),
    }))
    expectSuccess(await engine.dispatch({
      type: 'set.start',
      sessionId: 'session-1',
      expectedRevision: 0,
    }))
    clock.advance(1_000)
    expectSuccess(await engine.dispatch({
      type: 'set.complete',
      sessionId: 'session-1',
      expectedRevision: 1,
    }))
    expectSuccess(await engine.dispatch({
      type: 'set.start',
      sessionId: 'session-1',
      expectedRevision: 2,
    }))

    clock.advance(500)
    const skipped = expectSuccess(await engine.dispatch({
      type: 'action.skip',
      sessionId: 'session-1',
      expectedRevision: 3,
    }))
    expect(skipped.session).toMatchObject({
      revision: 4,
      status: 'paused',
      pauseReason: 'between_actions',
      currentItemIndex: 1,
      currentSetIndex: 0,
      progress: [
        { itemId: 'curl', completedSets: 1, activeMilliseconds: 1_500, skipped: true },
        { itemId: 'plank', completedSets: 0, activeMilliseconds: 0, skipped: false },
      ],
    })
    expect(skipped.events).toEqual([{ type: 'action.skipped', itemId: 'curl' }])

    expectSuccess(await engine.dispatch({
      type: 'set.start',
      sessionId: 'session-1',
      expectedRevision: 4,
    }))
    clock.advance(400)
    const ended = expectSuccess(await engine.dispatch({
      type: 'session.end_early',
      sessionId: 'session-1',
      expectedRevision: 5,
    }))
    expect(ended.session).toBeNull()
    expect(ended.record).toMatchObject({
      outcome: 'ended_early',
      activeSeconds: 2,
      completedActionCount: 0,
      actions: [
        { itemId: 'curl', completedSets: 1, completedReps: 10, status: 'partial' },
        { itemId: 'plank', completedSets: 0, completedDurationSeconds: 0, status: 'skipped' },
      ],
    })
    expect(ended.events).toEqual([{ type: 'session.ended_early', recordId: 'session-1' }])
  })
})
