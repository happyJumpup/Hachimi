import type { HachimiDatabase } from '@/db/hachimi-database'
import { database } from '@/db/hachimi-database'
import type { TrainingProfile, TrainingRecord, TrainingSession } from '@/domain/training'

export type CreateCurrentResult =
  | { status: 'created'; session: TrainingSession }
  | { status: 'exists'; session: TrainingSession }

export type TrainingCommit = {
  sessionId: string
  expectedRevision: number
} & (
  | { nextSession: TrainingSession; record?: never }
  | { nextSession?: never; record: TrainingRecord }
)

export type CommitResult =
  | { status: 'committed'; session: TrainingSession; record: null }
  | { status: 'committed'; session: null; record: TrainingRecord }
  | { status: 'conflict'; session: TrainingSession }
  | { status: 'missing' }
  | { status: 'already_finalized'; record: TrainingRecord }

export interface TrainingPersistence {
  loadCurrent(): Promise<TrainingSession | null>
  loadRecord(sessionId: string): Promise<TrainingRecord | null>
  loadProfile(): Promise<TrainingProfile | null>
  createCurrent(session: TrainingSession): Promise<CreateCurrentResult>
  commit(change: TrainingCommit): Promise<CommitResult>
}

export const createDexieTrainingRepository = (
  db: HachimiDatabase,
): TrainingPersistence => ({
  async loadCurrent() {
    return (await db.sessions.get('current')) ?? null
  },

  async loadRecord(sessionId) {
    return (await db.records.get(sessionId)) ?? null
  },

  async loadProfile() {
    return (await db.profiles.get('current')) ?? null
  },

  async createCurrent(session) {
    return db.transaction('rw', db.sessions, async () => {
      const existing = await db.sessions.get('current')
      if (existing) return { status: 'exists' as const, session: existing }

      await db.sessions.add(session)
      return { status: 'created' as const, session }
    })
  },

  async commit(change) {
    return db.transaction('rw', db.sessions, db.records, async () => {
      const existingRecord = await db.records.get(change.sessionId)
      if (existingRecord) {
        return { status: 'already_finalized' as const, record: existingRecord }
      }

      const current = await db.sessions.get('current')
      if (!current) return { status: 'missing' as const }
      if (
        current.sessionId !== change.sessionId
        || current.revision !== change.expectedRevision
      ) {
        return { status: 'conflict' as const, session: current }
      }

      if (change.record !== undefined) {
        await db.records.add(change.record)
        await db.sessions.delete('current')
        return { status: 'committed' as const, session: null, record: change.record }
      }

      if (
        change.nextSession.id !== 'current'
        || change.nextSession.sessionId !== current.sessionId
        || change.nextSession.revision !== current.revision + 1
      ) {
        throw new Error('invalid training session commit')
      }

      await db.sessions.put(change.nextSession)
      return { status: 'committed' as const, session: change.nextSession, record: null }
    })
  },
})

export const trainingRepository = createDexieTrainingRepository(database)
