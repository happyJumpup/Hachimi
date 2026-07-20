import type { DraftRepository } from '@/domain/types'
import { database } from '@/db/hachimi-database'

export const draftRepository: DraftRepository = {
  async load() {
    return database.drafts.get('current')
  },
  async save(plan) {
    await database.drafts.put(plan)
  },
}
