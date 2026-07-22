import 'fake-indexeddb/auto'

import Dexie from 'dexie'
import { afterEach, describe, expect, it } from 'vitest'

import { createHachimiDatabase } from '@/db/hachimi-database'
import { createDexieLocalMediaRepository } from '@/db/local-media-repository'

const databases: string[] = []

const setup = () => {
  const name = `local-media-${crypto.randomUUID()}`
  databases.push(name)
  const db = createHachimiDatabase(name)
  return { db, repository: createDexieLocalMediaRepository(db) }
}

afterEach(async () => {
  await Promise.all(databases.splice(0).map((name) => Dexie.delete(name)))
})

describe('local media repository', () => {
  it('restores the latest imported Blob and its validation fingerprint', async () => {
    const { repository } = setup()
    const older = new File(['old'], '旧视频.mp4', { type: 'video/mp4', lastModified: 1 })
    const latest = new File(['latest'], '训练.mp4', { type: 'video/mp4', lastModified: 2 })

    await repository.save({
      sourceId: 'local:11111111-1111-4111-8111-111111111111',
      blob: older,
      fileName: older.name,
      mimeType: older.type,
      sizeBytes: older.size,
      lastModified: older.lastModified,
      durationSeconds: 12,
      importedAt: '2026-07-21T15:00:00.000Z',
      updatedAt: '2026-07-21T15:00:00.000Z',
    })
    await repository.save({
      sourceId: 'local:22222222-2222-4222-8222-222222222222',
      blob: latest,
      fileName: latest.name,
      mimeType: latest.type,
      sizeBytes: latest.size,
      lastModified: latest.lastModified,
      durationSeconds: 34,
      importedAt: '2026-07-21T16:00:00.000Z',
      updatedAt: '2026-07-21T16:00:00.000Z',
    })

    const restored = await repository.loadLatest()
    expect(restored).toMatchObject({
      sourceId: 'local:22222222-2222-4222-8222-222222222222',
      fileName: '训练.mp4',
      durationSeconds: 34,
    })
    expect(restored?.blob).toBeDefined()
  })

  it('replaces a missing Blob only when the selected file matches basic metadata', async () => {
    const { repository } = setup()
    const expected = {
      fileName: '训练.mp4',
      mimeType: 'video/mp4',
      sizeBytes: 5,
      lastModified: 123,
      durationSeconds: 20,
    }
    const matching = new File(['12345'], expected.fileName, {
      type: expected.mimeType,
      lastModified: expected.lastModified,
    })

    await expect(repository.replaceFromSelection(
      'local:11111111-1111-4111-8111-111111111111',
      expected,
      matching,
      20.2,
    )).resolves.toMatchObject({ sourceId: 'local:11111111-1111-4111-8111-111111111111' })

    const different = new File(['x'], '其他.mp4', { type: 'video/mp4', lastModified: 123 })
    await expect(repository.replaceFromSelection(
      'local:11111111-1111-4111-8111-111111111111',
      expected,
      different,
      20,
    )).rejects.toThrow('local media selection does not match')
  })
})
