import 'fake-indexeddb/auto'

import Dexie from 'dexie'
import { afterEach, describe, expect, it } from 'vitest'

import { createDexieDraftRepository } from '@/db/draft-repository'
import { createHachimiDatabase } from '@/db/hachimi-database'
import { createDexieLibraryRepository } from '@/db/library-repository'
import type { DraftPlan } from '@/domain/types'
import { createQuickExperienceDraftItems } from '@/features/quick-experience/fixture'
import { LocalDataEpochFence, type EpochStorage } from '@/local-data/epoch-fence'

const databases: string[] = []

const setup = () => {
  const name = `hachimi-fitness-library-${crypto.randomUUID()}`
  databases.push(name)
  const database = createHachimiDatabase(name)
  const library = createDexieLibraryRepository(database, {
    createId: () => 'plan-b',
    now: () => new Date('2026-07-21T01:00:00.000Z'),
  })
  return { database, draftRepository: createDexieDraftRepository(database), library }
}

const draft = (): DraftPlan => ({
  id: 'current',
  name: '手臂计划 A',
  linkedPlanId: null,
  items: createQuickExperienceDraftItems(() => crypto.randomUUID()),
  updatedAt: '2026-07-21T00:00:00.000Z',
})

afterEach(async () => {
  await Promise.all(databases.splice(0).map((name) => Dexie.delete(name)))
})

describe('training library repository', () => {
  it('saves as a named plan and directs later draft edits to that plan', async () => {
    const { database, draftRepository, library } = setup()
    await draftRepository.save(draft())

    const saved = await library.saveCurrentDraftAs('手臂计划 A')
    expect(saved.plan.id).toBe('plan-b')
    expect(saved.draft.linkedPlanId).toBe('plan-b')

    saved.draft.items[0]!.name = '直接修改原方案'
    saved.draft.updatedAt = '2026-07-21T01:05:00.000Z'
    await draftRepository.save(saved.draft)

    expect((await database.plans.get('plan-b'))?.items[0]?.name).toBe('直接修改原方案')
    database.close()
  })

  it('opens a saved plan as current and can replace it with an unlinked quick sample', async () => {
    const { database, draftRepository, library } = setup()
    await draftRepository.save(draft())
    await library.saveCurrentDraftAs('手臂计划 A')

    const opened = await library.openPlan('plan-b')
    expect(opened.linkedPlanId).toBe('plan-b')

    const quick = await library.replaceCurrentDraft({
      name: '8 分钟手臂唤醒',
      items: createQuickExperienceDraftItems(() => crypto.randomUUID()),
    })
    expect(quick.linkedPlanId).toBeNull()
    expect((await database.plans.get('plan-b'))?.name).toBe('手臂计划 A')
    database.close()
  })

  it('persists profile and preference, then clears every local table explicitly', async () => {
    const { database, draftRepository, library } = setup()
    await draftRepository.save(draft())
    await library.saveProfile({ sex: 'female', age: 28, heightCm: 165, weightKg: 55 })
    await library.savePreferences({ petVisible: false })

    expect(await library.loadProfile()).toMatchObject({ id: 'current', sex: 'female' })
    expect(await library.loadPreferences()).toMatchObject({ id: 'current', petVisible: false })

    await library.clearAllLocalData()
    expect(await Promise.all(database.tables.map((table) => table.count()))).toEqual([0, 0, 0, 0, 0, 0])
    database.close()
  })

  it('rejects a stale tab preference write after a newer clear epoch emptied the tables', async () => {
    const values = new Map<string, string>()
    const storage: EpochStorage = {
      getItem: (key) => values.get(key) ?? null,
      setItem: (key, value) => { values.set(key, value) },
    }
    const staleFence = new LocalDataEpochFence(storage)
    const clearingFence = new LocalDataEpochFence(storage)
    const name = `hachimi-fitness-library-${crypto.randomUUID()}`
    databases.push(name)
    const database = createHachimiDatabase(name)
    const staleTab = createDexieLibraryRepository(database, { writeFence: staleFence })
    await staleTab.savePreferences({ petVisible: false })

    clearingFence.beginClear('epoch-2')
    await staleTab.clearAllLocalData()

    await expect(staleTab.savePreferences({ petVisible: true })).rejects.toThrow(
      /stale local data epoch/,
    )
    expect(await database.preferences.count()).toBe(0)
    database.close()
  })
})
