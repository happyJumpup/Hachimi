import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { LocalMediaRecord, LocalMediaRepository } from '@/domain/types'
import { useLocalMediaStore } from '@/stores/local-media'

const file = () => new File(['video'], '训练.mp4', {
  type: 'video/mp4',
  lastModified: 123,
})

const memoryRepository = (): LocalMediaRepository & { records: Map<string, LocalMediaRecord> } => {
  const records = new Map<string, LocalMediaRecord>()
  return {
    records,
    async load(sourceId) { return records.get(sourceId) },
    async loadLatest() { return [...records.values()].at(-1) },
    async save(record) { records.set(record.sourceId, record) },
    async replaceFromSelection(sourceId, expected, selected, durationSeconds) {
      const record: LocalMediaRecord = {
        sourceId,
        blob: selected,
        ...expected,
        durationSeconds,
        importedAt: new Date(0).toISOString(),
        updatedAt: new Date(0).toISOString(),
      }
      records.set(sourceId, record)
      return record
    },
  }
}

describe('local media store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL: vi.fn((blob: Blob) => `blob:test-${blob.size}`),
      revokeObjectURL: vi.fn(),
    })
  })

  it('keeps the selected video playable for the current session when IndexedDB save fails', async () => {
    const repository = memoryRepository()
    repository.save = async () => { throw new Error('quota exceeded') }
    const store = useLocalMediaStore()

    const record = await store.importFile({
      file: file(),
      durationSeconds: 42,
      repository,
      sourceId: 'local:11111111-1111-4111-8111-111111111111',
    })

    expect(record.sourceId).toBe('local:11111111-1111-4111-8111-111111111111')
    expect(store.storageStatus).toBe('session_only')
    expect(store.urlFor(record.sourceId)).toBe('blob:test-5')
    expect(store.storageMessage).toContain('当前打开期间')
  })

  it('restores a persisted local video and creates a fresh object URL after refresh', async () => {
    const repository = memoryRepository()
    const selected = file()
    await repository.save({
      sourceId: 'local:11111111-1111-4111-8111-111111111111',
      blob: selected,
      fileName: selected.name,
      mimeType: selected.type,
      sizeBytes: selected.size,
      lastModified: selected.lastModified,
      durationSeconds: 42,
      importedAt: '2026-07-21T16:00:00.000Z',
      updatedAt: '2026-07-21T16:00:00.000Z',
    })
    const store = useLocalMediaStore()

    await expect(store.restore({ repository })).resolves.toMatchObject({
      fileName: '训练.mp4',
      durationSeconds: 42,
    })
    expect(store.urlFor('local:11111111-1111-4111-8111-111111111111')).toBe('blob:test-5')
    expect(store.storageStatus).toBe('saved')
  })

  it('reports a missing local video without blocking the caller', async () => {
    const store = useLocalMediaStore()

    await expect(store.resolve(
      'local:11111111-1111-4111-8111-111111111111',
      memoryRepository(),
    )).resolves.toBeNull()
    expect(store.missingSourceIds).toContain('local:11111111-1111-4111-8111-111111111111')
  })
})
