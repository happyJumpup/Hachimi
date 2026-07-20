import 'fake-indexeddb/auto'

import Dexie from 'dexie'
import { afterEach, describe, expect, it } from 'vitest'

import { createHachimiDatabase } from '@/db/hachimi-database'
import type { DraftPlan } from '@/domain/types'

const databases: string[] = []

const uniqueDatabaseName = (): string => {
  const name = `hachimi-fitness-test-${crypto.randomUUID()}`
  databases.push(name)
  return name
}

afterEach(async () => {
  await Promise.all(databases.splice(0).map((name) => Dexie.delete(name)))
})

describe('Dexie v2 local training data', () => {
  it('upgrades the v1 current draft without changing its actions or field sources', async () => {
    const databaseName = uniqueDatabaseName()
    const legacy = new Dexie(databaseName)
    legacy.version(1).stores({ drafts: '&id, updatedAt' })

    const legacyDraft: Omit<DraftPlan, 'name' | 'linkedPlanId'> = {
      id: 'current',
      items: [
        {
          id: 'video-action',
          name: '拖拽弯举',
          sourceRef: { sourceId: 'video-a', title: '来源视频 A' },
          segment: {
            value: { start_seconds: 41, end_seconds: 51 },
            source: 'video',
          },
          mode: 'reps',
          sets: { value: 3, source: 'rule' },
          reps: { value: 10, source: 'rule' },
          durationSeconds: { value: null, source: null },
          restSeconds: { value: 60, source: 'video' },
          weightKg: { value: null, source: null },
        },
        {
          id: 'manual-action',
          name: '平板支撑',
          sourceRef: null,
          segment: { value: null, source: null },
          mode: 'duration',
          sets: { value: 2, source: 'user' },
          reps: { value: null, source: null },
          durationSeconds: { value: 30, source: 'user' },
          restSeconds: { value: 0, source: 'user' },
          weightKg: { value: null, source: null },
        },
      ],
      updatedAt: '2026-07-20T08:00:00.000Z',
    }
    await legacy.table('drafts').put(legacyDraft)
    legacy.close()

    const database = createHachimiDatabase(databaseName)
    await database.open()

    const migrated = await database.drafts.get('current')
    expect(migrated).toEqual<DraftPlan>({
      ...legacyDraft,
      name: '未命名方案',
      linkedPlanId: null,
    })
    expect(database.tables.map((table) => table.name).sort()).toEqual([
      'drafts',
      'plans',
      'preferences',
      'profiles',
      'records',
      'sessions',
    ])

    database.close()
  })
})
