import Dexie, { type Table, type Transaction } from 'dexie'

import type { DraftPlan, LocalMediaRecord } from '@/domain/types'
import type {
  Preferences,
  SavedPlan,
  TrainingProfile,
  TrainingRecord,
  TrainingSession,
} from '@/domain/training'

export class HachimiDatabase extends Dexie {
  drafts!: Table<DraftPlan, string>
  plans!: Table<SavedPlan, string>
  sessions!: Table<TrainingSession, string>
  records!: Table<TrainingRecord, string>
  profiles!: Table<TrainingProfile, string>
  preferences!: Table<Preferences, string>
  localMedia!: Table<LocalMediaRecord, string>

  constructor(name = 'hachimi-fitness') {
    super(name)

    this.version(1).stores({
      drafts: '&id, updatedAt',
    })

    this.version(2).stores({
      drafts: '&id, updatedAt, linkedPlanId',
      plans: '&id, updatedAt, createdAt, name',
      sessions: '&id, sessionId, status, updatedAt',
      records: '&id, endedAt, outcome',
      profiles: '&id, updatedAt',
      preferences: '&id, updatedAt',
    }).upgrade(async (transaction: Transaction) => {
      await transaction.table('drafts').toCollection().modify((draft: Partial<DraftPlan>) => {
        if (draft.id !== 'current') return
        draft.name = '未命名方案'
        draft.linkedPlanId = null
      })
    })

    this.version(3).stores({
      drafts: '&id, updatedAt, linkedPlanId',
      plans: '&id, updatedAt, createdAt, name',
      sessions: '&id, sessionId, status, updatedAt',
      records: '&id, endedAt, outcome',
      profiles: '&id, updatedAt',
      preferences: '&id, updatedAt',
      localMedia: '&sourceId, importedAt, updatedAt',
    })
  }
}

export const createHachimiDatabase = (name?: string): HachimiDatabase =>
  new HachimiDatabase(name)

export const database = createHachimiDatabase()
