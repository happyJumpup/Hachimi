import { computed, ref, shallowRef } from 'vue'
import { defineStore } from 'pinia'

import { localMediaRepository } from '@/db/local-media-repository'
import type {
  LocalMediaFingerprint,
  LocalMediaRecord,
  LocalMediaRepository,
} from '@/domain/types'

type StorageStatus = 'idle' | 'saved' | 'session_only'

const makeSourceId = (): string => `local:${crypto.randomUUID().toLowerCase()}`

export const useLocalMediaStore = defineStore('local-media', () => {
  const current = shallowRef<LocalMediaRecord | null>(null)
  const storageStatus = ref<StorageStatus>('idle')
  const urls = ref<Record<string, string>>({})
  const missingSourceIds = ref<string[]>([])

  const storageMessage = computed(() => {
    if (storageStatus.value === 'saved') return '视频已保存在本机，可在刷新后继续使用'
    if (storageStatus.value === 'session_only') {
      return '本机存储失败；当前打开期间仍可继续，关闭后可能需要重新选择视频'
    }
    return ''
  })

  const urlFor = (sourceId: string): string | null => urls.value[sourceId] ?? null

  const removeMissing = (sourceId: string): void => {
    missingSourceIds.value = missingSourceIds.value.filter((id) => id !== sourceId)
  }

  const adopt = (record: LocalMediaRecord): LocalMediaRecord => {
    const previousUrl = urls.value[record.sourceId]
    if (previousUrl) URL.revokeObjectURL(previousUrl)
    urls.value = {
      ...urls.value,
      [record.sourceId]: URL.createObjectURL(record.blob),
    }
    current.value = record
    removeMissing(record.sourceId)
    return record
  }

  async function importFile(input: {
    file: File
    durationSeconds: number
    repository?: LocalMediaRepository
    sourceId?: string
    now?: Date
  }): Promise<LocalMediaRecord> {
    const repository = input.repository ?? localMediaRepository
    const timestamp = (input.now ?? new Date()).toISOString()
    const record: LocalMediaRecord = {
      sourceId: input.sourceId ?? makeSourceId(),
      blob: input.file,
      fileName: input.file.name,
      mimeType: input.file.type,
      sizeBytes: input.file.size,
      lastModified: input.file.lastModified,
      durationSeconds: input.durationSeconds,
      importedAt: timestamp,
      updatedAt: timestamp,
    }
    adopt(record)
    try {
      await repository.save(record)
      storageStatus.value = 'saved'
    } catch {
      storageStatus.value = 'session_only'
    }
    return record
  }

  async function restore(input: {
    repository?: LocalMediaRepository
    sourceId?: string
  } = {}): Promise<LocalMediaRecord | null> {
    const repository = input.repository ?? localMediaRepository
    let record: LocalMediaRecord | undefined
    try {
      record = input.sourceId
        ? await repository.load(input.sourceId)
        : await repository.loadLatest()
    } catch {
      record = undefined
    }
    if (!record) {
      if (input.sourceId && !missingSourceIds.value.includes(input.sourceId)) {
        missingSourceIds.value = [...missingSourceIds.value, input.sourceId]
      }
      return null
    }
    storageStatus.value = 'saved'
    return adopt(record)
  }

  async function resolve(
    sourceId: string,
    repository: LocalMediaRepository = localMediaRepository,
  ): Promise<string | null> {
    const existing = urlFor(sourceId)
    if (existing) return existing
    const restored = await restore({ repository, sourceId })
    return restored ? urlFor(sourceId) : null
  }

  async function replaceFromSelection(input: {
    sourceId: string
    expected: LocalMediaFingerprint
    file: File
    durationSeconds: number
    repository?: LocalMediaRepository
  }): Promise<LocalMediaRecord> {
    const repository = input.repository ?? localMediaRepository
    const record = await repository.replaceFromSelection(
      input.sourceId,
      input.expected,
      input.file,
      input.durationSeconds,
    )
    storageStatus.value = 'saved'
    return adopt(record)
  }

  function resetLocalState(): void {
    for (const url of Object.values(urls.value)) URL.revokeObjectURL(url)
    urls.value = {}
    current.value = null
    missingSourceIds.value = []
    storageStatus.value = 'idle'
  }

  return {
    current,
    storageStatus,
    storageMessage,
    missingSourceIds,
    urlFor,
    importFile,
    restore,
    resolve,
    replaceFromSelection,
    resetLocalState,
  }
})
