import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { AnalysisCandidate, DraftPlan, DraftRepository } from '@/domain/types'
import { useDraftStore } from '@/stores/draft'

class MemoryDraftRepository implements DraftRepository {
  value: DraftPlan | undefined
  saveCount = 0

  async load(): Promise<DraftPlan | undefined> {
    return structuredClone(this.value)
  }

  async save(plan: DraftPlan): Promise<void> {
    this.value = structuredClone(plan)
    this.saveCount += 1
  }
}

function candidate(sourceId: string): AnalysisCandidate {
  return {
    id: `${sourceId}-candidate`,
    name: '拖拽弯举',
    source_id: sourceId,
    segment: { start_seconds: 41, end_seconds: 51 },
    parameters: {
      mode: 'reps',
      sets: null,
      reps: null,
      duration_seconds: null,
      rest_seconds: null,
    },
    evidence: [
      { type: 'speech', start_seconds: 42, end_seconds: 49 },
      { type: 'visual', start_seconds: 41, end_seconds: 51 },
    ],
    needs_confirmation: false,
  }
}

describe('方案草稿 store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.useFakeTimers()
  })

  it('applies visible rule defaults and persists actions from multiple videos', async () => {
    const repository = new MemoryDraftRepository()
    const store = useDraftStore()
    await store.load(repository)

    store.addCandidates([candidate('video-a'), candidate('video-b')], [], {
      'video-a': '来源视频 A',
      'video-b': '来源视频 B',
    })

    expect(store.items).toHaveLength(2)
    expect(store.items.map((item) => item.sourceRef?.sourceId)).toEqual(['video-a', 'video-b'])
    expect(store.items.map((item) => item.sourceRef?.title)).toEqual(['来源视频 A', '来源视频 B'])
    expect(store.items[0].sets).toEqual({ value: 3, source: 'rule' })
    expect(store.items[0].reps).toEqual({ value: 10, source: 'rule' })
    expect(store.items[0].restSeconds).toEqual({ value: 60, source: 'rule' })
    expect(store.items[0].weightKg).toEqual({ value: null, source: null })

    store.updateValue(store.items[0].id, 'sets', 4)
    expect(store.items[0].sets).toEqual({ value: 4, source: 'user' })

    await vi.advanceTimersByTimeAsync(301)
    expect(repository.saveCount).toBe(1)
    expect(repository.value?.items).toHaveLength(2)
  })

  it('supports manual actions, duplicate, reorder, delete, and reload', async () => {
    const repository = new MemoryDraftRepository()
    const store = useDraftStore()
    await store.load(repository)

    store.addManualAction({ name: '平板支撑', mode: 'duration' })
    const originalId = store.items[0].id
    expect(store.items[0].sourceRef).toBeNull()
    expect(store.items[0].durationSeconds).toEqual({ value: 30, source: 'rule' })

    store.duplicate(originalId)
    expect(store.items).toHaveLength(2)
    expect(store.items[1].id).not.toBe(originalId)
    store.move(store.items[1].id, -1)
    expect(store.items[0].id).not.toBe(originalId)
    store.remove(originalId)
    await store.flushPersist()

    setActivePinia(createPinia())
    const reloaded = useDraftStore()
    await reloaded.load(repository)
    expect(reloaded.items).toHaveLength(1)
    expect(reloaded.items[0].name).toBe('平板支撑（副本）')
  })
})
