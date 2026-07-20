import type { DraftRepository } from '@/domain/types'
import type { HachimiDatabase } from '@/db/hachimi-database'
import { database } from '@/db/hachimi-database'
import { localDataEpochFence, type LocalDataEpochFence } from '@/local-data/epoch-fence'

export const createDexieDraftRepository = (
  db: HachimiDatabase,
  writeFence: LocalDataEpochFence = localDataEpochFence,
): DraftRepository => ({
  async load() {
    return db.drafts.get('current')
  },
  async save(plan) {
    writeFence.assertWritable()
    await db.transaction('rw', db.drafts, db.plans, async () => {
      await db.drafts.put(plan)
      if (!plan.linkedPlanId) return

      const linkedPlan = await db.plans.get(plan.linkedPlanId)
      if (!linkedPlan) throw new Error('linked plan does not exist')
      await db.plans.put({
        ...linkedPlan,
        name: plan.name,
        items: structuredClone(plan.items),
        updatedAt: plan.updatedAt,
      })
    })
  },
})

export const draftRepository = createDexieDraftRepository(database)
