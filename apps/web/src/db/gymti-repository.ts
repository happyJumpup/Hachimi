import type { HachimiDatabase } from '@/db/hachimi-database'
import { database } from '@/db/hachimi-database'
import type {
  CurrentGymtiResult,
  GymtiAttempt,
  GymtiNarrativeSnapshot,
  PendingGymtiResult,
} from '@/domain/gymti'
import { localDataEpochFence, type LocalDataEpochFence } from '@/local-data/epoch-fence'

export interface GymtiLifecycleState {
  attempt: GymtiAttempt | null
  pending: PendingGymtiResult | null
  current: CurrentGymtiResult | null
}

export interface GymtiRepository {
  loadState(): Promise<GymtiLifecycleState>
  startAttempt(attempt: GymtiAttempt): Promise<void>
  saveAttempt(attempt: GymtiAttempt): Promise<void>
  discardAttempt(attemptId: string): Promise<void>
  saveCompletedAttempt(attempt: GymtiAttempt, result: PendingGymtiResult): Promise<void>
  reopenAttempt(attempt: GymtiAttempt): Promise<void>
  savePendingResult(result: PendingGymtiResult): Promise<void>
  attachNarrative(
    resultId: string,
    narrative: GymtiNarrativeSnapshot,
  ): Promise<PendingGymtiResult>
  activatePendingResult(resultId: string): Promise<CurrentGymtiResult>
}

interface GymtiRepositoryOptions {
  now?: () => Date
  writeFence?: LocalDataEpochFence
}

export const createDexieGymtiRepository = (
  db: HachimiDatabase,
  {
    now = () => new Date(),
    writeFence = localDataEpochFence,
  }: GymtiRepositoryOptions = {},
): GymtiRepository => ({
  async loadState() {
    const [attempt, pending, current] = await db.transaction(
      'r',
      db.gymtiAttempts,
      db.gymtiPendingResults,
      db.gymtiResults,
      () => Promise.all([
        db.gymtiAttempts.get('current'),
        db.gymtiPendingResults.get('current'),
        db.gymtiResults.get('current'),
      ]),
    )
    return {
      attempt: attempt ?? null,
      pending: pending ?? null,
      current: current ?? null,
    }
  },

  async saveAttempt(attempt) {
    writeFence.assertWritable()
    await db.gymtiAttempts.put(structuredClone(attempt))
  },

  async startAttempt(attempt) {
    writeFence.assertWritable()
    await db.transaction('rw', db.gymtiAttempts, db.gymtiPendingResults, async () => {
      await db.gymtiPendingResults.delete('current')
      await db.gymtiAttempts.put(structuredClone(attempt))
    })
  },

  async discardAttempt(attemptId) {
    writeFence.assertWritable()
    await db.transaction('rw', db.gymtiAttempts, db.gymtiPendingResults, async () => {
      const attempt = await db.gymtiAttempts.get('current')
      if (!attempt || attempt.attemptId !== attemptId) return
      await Promise.all([
        db.gymtiAttempts.delete('current'),
        db.gymtiPendingResults
          .where('attemptId')
          .equals(attemptId)
          .delete(),
      ])
    })
  },

  async savePendingResult(result) {
    writeFence.assertWritable()
    await db.gymtiPendingResults.put(structuredClone(result))
  },

  async saveCompletedAttempt(attempt, result) {
    writeFence.assertWritable()
    if (attempt.attemptId !== result.attemptId || attempt.phase !== 'profile') {
      throw new Error('completed GYMTI attempt and pending result do not match')
    }
    await db.transaction('rw', db.gymtiAttempts, db.gymtiPendingResults, async () => {
      await Promise.all([
        db.gymtiAttempts.put(structuredClone(attempt)),
        db.gymtiPendingResults.put(structuredClone(result)),
      ])
    })
  },

  async reopenAttempt(attempt) {
    writeFence.assertWritable()
    if (attempt.phase !== 'questionnaire') {
      throw new Error('reopened GYMTI attempt must return to questionnaire phase')
    }
    await db.transaction('rw', db.gymtiAttempts, db.gymtiPendingResults, async () => {
      await Promise.all([
        db.gymtiAttempts.put(structuredClone(attempt)),
        db.gymtiPendingResults.delete('current'),
      ])
    })
  },

  async attachNarrative(resultId, narrative) {
    writeFence.assertWritable()
    return db.transaction('rw', db.gymtiPendingResults, async () => {
      const pending = await db.gymtiPendingResults.get('current')
      if (!pending || pending.resultId !== resultId) {
        throw new Error('pending GYMTI result does not exist')
      }
      if (pending.narrative !== null) {
        throw new Error('pending GYMTI result already has a narrative')
      }
      const next: PendingGymtiResult = {
        ...pending,
        narrative: structuredClone(narrative),
        updatedAt: now().toISOString(),
      }
      await db.gymtiPendingResults.put(next)
      return next
    })
  },

  async activatePendingResult(resultId) {
    writeFence.assertWritable()
    return db.transaction(
      'rw',
      db.gymtiAttempts,
      db.gymtiPendingResults,
      db.gymtiResults,
      async () => {
        const pending = await db.gymtiPendingResults.get('current')
        if (!pending || pending.resultId !== resultId) {
          throw new Error('pending GYMTI result does not exist')
        }
        if (pending.narrative === null) {
          throw new Error('pending GYMTI result does not have a narrative')
        }

        const activatedAt = now().toISOString()
        const current: CurrentGymtiResult = {
          ...pending,
          narrative: pending.narrative,
          activatedAt,
          updatedAt: activatedAt,
        }
        await db.gymtiResults.delete('current')
        await db.gymtiResults.put(current)
        await Promise.all([
          db.gymtiAttempts.delete('current'),
          db.gymtiPendingResults.delete('current'),
        ])
        return current
      },
    )
  },
})

export const gymtiRepository = createDexieGymtiRepository(database)
