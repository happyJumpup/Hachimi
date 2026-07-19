import Dexie, { type Table } from 'dexie'

import type { DraftPlan, DraftRepository } from '@/domain/types'

class HachimiDatabase extends Dexie {
  drafts!: Table<DraftPlan, string>

  constructor() {
    super('hachimi-fitness')
    this.version(1).stores({
      drafts: '&id, updatedAt',
    })
  }
}

const database = new HachimiDatabase()

export const draftRepository: DraftRepository = {
  async load() {
    return database.drafts.get('current')
  },
  async save(plan) {
    await database.drafts.put(plan)
  },
}
