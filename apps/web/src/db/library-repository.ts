import type { HachimiDatabase } from '@/db/hachimi-database'
import { database } from '@/db/hachimi-database'
import type { DraftItem, DraftPlan } from '@/domain/types'
import type {
  Preferences,
  SavedPlan,
  TrainingProfile,
  TrainingRecord,
} from '@/domain/training'

export interface LibraryRepository {
  listPlans(): Promise<SavedPlan[]>
  listRecords(): Promise<TrainingRecord[]>
  loadProfile(): Promise<TrainingProfile | null>
  loadPreferences(): Promise<Preferences | null>
  saveProfile(profile: Omit<TrainingProfile, 'id' | 'updatedAt'>): Promise<TrainingProfile>
  savePreferences(preferences: Omit<Preferences, 'id' | 'updatedAt'>): Promise<Preferences>
  saveCurrentDraftAs(name: string): Promise<{ plan: SavedPlan; draft: DraftPlan }>
  openPlan(planId: string): Promise<DraftPlan>
  replaceCurrentDraft(input: { name: string; items: DraftItem[] }): Promise<DraftPlan>
  clearAllLocalData(): Promise<void>
}

interface LibraryRepositoryOptions {
  createId?: () => string
  now?: () => Date
}

export const createDexieLibraryRepository = (
  db: HachimiDatabase,
  {
    createId = () => crypto.randomUUID(),
    now = () => new Date(),
  }: LibraryRepositoryOptions = {},
): LibraryRepository => ({
  async listPlans() {
    return db.plans.orderBy('updatedAt').reverse().toArray()
  },

  async listRecords() {
    return db.records.orderBy('endedAt').reverse().toArray()
  },

  async loadProfile() {
    return (await db.profiles.get('current')) ?? null
  },

  async loadPreferences() {
    return (await db.preferences.get('current')) ?? null
  },

  async saveProfile(profile) {
    const saved: TrainingProfile = {
      ...profile,
      id: 'current',
      updatedAt: now().toISOString(),
    }
    await db.profiles.put(saved)
    return saved
  },

  async savePreferences(preferences) {
    const saved: Preferences = {
      ...preferences,
      id: 'current',
      updatedAt: now().toISOString(),
    }
    await db.preferences.put(saved)
    return saved
  },

  async saveCurrentDraftAs(name) {
    const normalizedName = name.trim()
    if (!normalizedName) throw new Error('plan name is required')

    return db.transaction('rw', db.drafts, db.plans, async () => {
      const current = await db.drafts.get('current')
      if (!current || !current.items.length) throw new Error('current draft is empty')

      const timestamp = now().toISOString()
      const plan: SavedPlan = {
        id: createId(),
        name: normalizedName,
        items: structuredClone(current.items),
        createdAt: timestamp,
        updatedAt: timestamp,
      }
      const nextDraft: DraftPlan = {
        ...structuredClone(current),
        name: normalizedName,
        linkedPlanId: plan.id,
        updatedAt: timestamp,
      }
      await db.plans.add(plan)
      await db.drafts.put(nextDraft)
      return { plan, draft: nextDraft }
    })
  },

  async openPlan(planId) {
    return db.transaction('rw', db.drafts, db.plans, async () => {
      const plan = await db.plans.get(planId)
      if (!plan) throw new Error('saved plan does not exist')
      const draft: DraftPlan = {
        id: 'current',
        name: plan.name,
        linkedPlanId: plan.id,
        items: structuredClone(plan.items),
        updatedAt: now().toISOString(),
      }
      await db.drafts.put(draft)
      return draft
    })
  },

  async replaceCurrentDraft(input) {
    const draft: DraftPlan = {
      id: 'current',
      name: input.name.trim() || '未命名方案',
      linkedPlanId: null,
      items: structuredClone(input.items),
      updatedAt: now().toISOString(),
    }
    await db.drafts.put(draft)
    return draft
  },

  async clearAllLocalData() {
    await db.transaction('rw', db.tables, async () => {
      await Promise.all(db.tables.map((table) => table.clear()))
    })
  },
})

export const libraryRepository = createDexieLibraryRepository(database)
