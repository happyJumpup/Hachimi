import type { HachimiDatabase } from '@/db/hachimi-database'
import { database } from '@/db/hachimi-database'
import { fingerprintMatches } from '@/domain/local-media'
import type {
  LocalMediaRecord,
  LocalMediaRepository,
} from '@/domain/types'
import { localDataEpochFence, type LocalDataEpochFence } from '@/local-data/epoch-fence'

export const createDexieLocalMediaRepository = (
  db: HachimiDatabase,
  writeFence: LocalDataEpochFence = localDataEpochFence,
): LocalMediaRepository => ({
  load(sourceId) {
    return db.localMedia.get(sourceId)
  },

  loadLatest() {
    return db.localMedia.orderBy('updatedAt').last()
  },

  async save(record) {
    writeFence.assertWritable()
    await db.localMedia.put(record)
  },

  async replaceFromSelection(sourceId, expected, file, durationSeconds) {
    if (!fingerprintMatches(expected, file, durationSeconds)) {
      throw new Error('local media selection does not match')
    }
    writeFence.assertWritable()
    const timestamp = new Date().toISOString()
    const existing = await db.localMedia.get(sourceId)
    const record: LocalMediaRecord = {
      sourceId,
      blob: file,
      ...expected,
      durationSeconds,
      importedAt: existing?.importedAt ?? timestamp,
      updatedAt: timestamp,
    }
    await db.localMedia.put(record)
    return record
  },
})

export const localMediaRepository = createDexieLocalMediaRepository(database)
