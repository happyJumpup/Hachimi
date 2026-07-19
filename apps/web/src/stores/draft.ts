import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import type {
  ActionMode,
  AnalysisCandidate,
  DraftItem,
  DraftPlan,
  DraftRepository,
  Segment,
  SourcedValue,
} from '@/domain/types'

type NumericField = 'sets' | 'reps' | 'durationSeconds' | 'restSeconds' | 'weightKg'

const emptyPlan = (): DraftPlan => ({
  id: 'current',
  items: [],
  updatedAt: new Date(0).toISOString(),
})

const id = (): string => globalThis.crypto?.randomUUID?.() ?? `item-${Date.now()}-${Math.random()}`

const cloneJson = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T

const sourced = <T>(value: T | null, source: SourcedValue<T>['source']): SourcedValue<T> => ({
  value,
  source,
})

const fromCandidate = (candidate: AnalysisCandidate, segmentEdited = false): DraftItem => {
  const mode = candidate.parameters.mode
  if (mode === null) {
    throw new Error('candidate mode must be selected before adding it to the draft')
  }
  const isReps = mode === 'reps'
  return {
    id: id(),
    name: candidate.name,
    sourceRef: { sourceId: candidate.source_id },
    segment: sourced(
      candidate.segment ? cloneJson(candidate.segment) : null,
      candidate.segment ? (segmentEdited ? 'user' : 'video') : null,
    ),
    mode,
    sets: candidate.parameters.sets === null
      ? sourced(3, 'rule')
      : sourced(candidate.parameters.sets, 'video'),
    reps: isReps
      ? candidate.parameters.reps === null
        ? sourced(10, 'rule')
        : sourced(candidate.parameters.reps, 'video')
      : sourced<number>(null, null),
    durationSeconds: !isReps
      ? candidate.parameters.duration_seconds === null
        ? sourced(30, 'rule')
        : sourced(candidate.parameters.duration_seconds, 'video')
      : sourced<number>(null, null),
    restSeconds: candidate.parameters.rest_seconds === null
      ? sourced(60, 'rule')
      : sourced(candidate.parameters.rest_seconds, 'video'),
    weightKg: sourced<number>(null, null),
  }
}

export const useDraftStore = defineStore('draft', () => {
  const plan = ref<DraftPlan>(emptyPlan())
  const loaded = ref(false)
  let repository: DraftRepository | undefined
  let persistTimer: ReturnType<typeof setTimeout> | undefined

  const items = computed(() => plan.value.items)

  async function load(nextRepository: DraftRepository): Promise<void> {
    repository = nextRepository
    plan.value = (await repository.load()) ?? emptyPlan()
    loaded.value = true
  }

  function schedulePersist(): void {
    if (!repository) {
      return
    }
    if (persistTimer) {
      clearTimeout(persistTimer)
    }
    persistTimer = setTimeout(() => {
      void flushPersist()
    }, 300)
  }

  async function flushPersist(): Promise<void> {
    if (!repository) {
      return
    }
    if (persistTimer) {
      clearTimeout(persistTimer)
      persistTimer = undefined
    }
    plan.value.updatedAt = new Date().toISOString()
    await repository.save(cloneJson(plan.value))
  }

  function addCandidates(candidates: AnalysisCandidate[], editedSegmentIds: string[] = []): void {
    const edited = new Set(editedSegmentIds)
    plan.value.items.push(...candidates.map((candidate) => fromCandidate(candidate, edited.has(candidate.id))))
    schedulePersist()
  }

  function addManualAction(input: { name: string; mode: ActionMode }): void {
    const isReps = input.mode === 'reps'
    plan.value.items.push({
      id: id(),
      name: input.name.trim(),
      sourceRef: null,
      segment: sourced<Segment>(null, null),
      mode: input.mode,
      sets: sourced(3, 'rule'),
      reps: isReps ? sourced(10, 'rule') : sourced<number>(null, null),
      durationSeconds: isReps ? sourced<number>(null, null) : sourced(30, 'rule'),
      restSeconds: sourced(60, 'rule'),
      weightKg: sourced<number>(null, null),
    })
    schedulePersist()
  }

  function updateValue(itemId: string, field: NumericField, value: number | null): void {
    const item = plan.value.items.find((entry) => entry.id === itemId)
    if (!item) {
      return
    }
    item[field] = sourced(value, value === null ? null : 'user')
    schedulePersist()
  }

  function updateName(itemId: string, name: string): void {
    const item = plan.value.items.find((entry) => entry.id === itemId)
    if (!item || !name.trim()) {
      return
    }
    item.name = name.trim()
    schedulePersist()
  }

  function updateMode(itemId: string, mode: ActionMode): void {
    const item = plan.value.items.find((entry) => entry.id === itemId)
    if (!item || item.mode === mode) {
      return
    }
    item.mode = mode
    item.reps = mode === 'reps' ? sourced(10, 'rule') : sourced<number>(null, null)
    item.durationSeconds = mode === 'duration' ? sourced(30, 'rule') : sourced<number>(null, null)
    schedulePersist()
  }

  function updateSegment(itemId: string, segment: Segment | null): void {
    const item = plan.value.items.find((entry) => entry.id === itemId)
    if (!item) {
      return
    }
    item.segment = sourced(segment ? cloneJson(segment) : null, segment ? 'user' : null)
    schedulePersist()
  }

  function move(itemId: string, direction: -1 | 1): void {
    const index = plan.value.items.findIndex((entry) => entry.id === itemId)
    const target = index + direction
    if (index < 0 || target < 0 || target >= plan.value.items.length) {
      return
    }
    const [item] = plan.value.items.splice(index, 1)
    plan.value.items.splice(target, 0, item)
    schedulePersist()
  }

  function duplicate(itemId: string): void {
    const index = plan.value.items.findIndex((entry) => entry.id === itemId)
    if (index < 0) {
      return
    }
    const copy = cloneJson(plan.value.items[index])
    copy.id = id()
    copy.name = `${copy.name}（副本）`
    plan.value.items.splice(index + 1, 0, copy)
    schedulePersist()
  }

  function remove(itemId: string): void {
    plan.value.items = plan.value.items.filter((entry) => entry.id !== itemId)
    schedulePersist()
  }

  return {
    plan,
    items,
    loaded,
    load,
    flushPersist,
    addCandidates,
    addManualAction,
    updateValue,
    updateName,
    updateMode,
    updateSegment,
    move,
    duplicate,
    remove,
  }
})
