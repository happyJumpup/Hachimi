import type { CoverageGap } from '@/domain/types'

export interface AnalysisRetryCheckpoint {
  runId: string
  gap: CoverageGap
}

export interface AnalysisCheckpoint {
  version: 1
  sourceId: string
  sourceKind: 'controlled' | 'local'
  rootRunId: string
  retries: AnalysisRetryCheckpoint[]
  lastSequences: Record<string, number>
}

export interface AnalysisCheckpointStore {
  load(): AnalysisCheckpoint | null
  save(checkpoint: AnalysisCheckpoint): void
  clear(): void
}

const STORAGE_KEY = 'hachimi-fitness:active-analysis'

const isCheckpoint = (value: unknown): value is AnalysisCheckpoint => {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Partial<AnalysisCheckpoint>
  return candidate.version === 1
    && typeof candidate.sourceId === 'string'
    && (candidate.sourceKind === 'controlled' || candidate.sourceKind === 'local')
    && typeof candidate.rootRunId === 'string'
    && Array.isArray(candidate.retries)
    && Boolean(candidate.lastSequences)
    && typeof candidate.lastSequences === 'object'
}

export const browserAnalysisCheckpointStore: AnalysisCheckpointStore = {
  load() {
    try {
      const value = globalThis.localStorage?.getItem(STORAGE_KEY)
      if (!value) return null
      const parsed: unknown = JSON.parse(value)
      return isCheckpoint(parsed) ? parsed : null
    } catch {
      return null
    }
  },

  save(checkpoint) {
    try {
      globalThis.localStorage?.setItem(STORAGE_KEY, JSON.stringify(checkpoint))
    } catch {
      // Recovery is best effort. The active in-memory run remains usable.
    }
  },

  clear() {
    try {
      globalThis.localStorage?.removeItem(STORAGE_KEY)
    } catch {
      // Clearing IndexedDB still proceeds when localStorage is unavailable.
    }
  },
}
