import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import type {
  ActionMode,
  AnalysisCandidate,
  DraftItem,
  DraftPlan,
  DraftRepository,
  LocalMediaFingerprint,
  Segment,
  SourceSummary,
  SourcedValue,
} from '@/domain/types'
import { toSafeOriginUrl } from '@/domain/source'

type NumericField = 'sets' | 'reps' | 'durationSeconds' | 'restSeconds' | 'weightKg'
type PersistState = 'idle' | 'pending' | 'saving' | 'saved' | 'failed'

const emptyPlan = (): DraftPlan => ({
  id: 'current',
  name: '未命名方案',
  linkedPlanId: null,
  items: [],
  updatedAt: new Date(0).toISOString(),
})

const id = (): string => globalThis.crypto?.randomUUID?.() ?? `item-${Date.now()}-${Math.random()}`

const cloneJson = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T

const sourced = <T>(value: T | null, source: SourcedValue<T>['source']): SourcedValue<T> => ({
  value,
  source,
})

type SourceSnapshot = Pick<SourceSummary, 'title' | 'origin_url'> & {
  kind?: 'controlled' | 'local'
  localMedia?: LocalMediaFingerprint
}

const fromCandidate = (
  candidate: AnalysisCandidate,
  segmentEdited = false,
  source?: SourceSnapshot,
  roleEdited = false,
): DraftItem => {
  const mode = candidate.parameters.mode
  if (mode === null) {
    throw new Error('candidate mode must be selected before adding it to the draft')
  }
  const isReps = mode === 'reps'
  return {
    id: id(),
    name: candidate.name,
    sourceRef: {
      sourceId: candidate.source_id,
      title: source?.title,
      originUrl: toSafeOriginUrl(source?.origin_url),
      ...(source?.kind ? { kind: source.kind } : {}),
      ...(source?.localMedia ? { localMedia: cloneJson(source.localMedia) } : {}),
    },
    segment: sourced(
      candidate.segment ? cloneJson(candidate.segment) : null,
      candidate.segment ? (segmentEdited ? 'user' : 'video') : null,
    ),
    segmentRole: sourced(
      candidate.segment_role ?? 'unknown',
      roleEdited ? 'user' : 'video',
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
  const persistState = ref<PersistState>('idle')
  let repository: DraftRepository | undefined
  let persistTimer: ReturnType<typeof setTimeout> | undefined
  let persistInFlight: Promise<void> | null = null
  let persistenceSuspended = false

  const items = computed(() => plan.value.items)
  const persistMessage = computed(() => {
    if (persistState.value === 'failed') return '未保存，点击重试'
    if (persistState.value === 'pending' || persistState.value === 'saving') {
      return '正在保存到本机…'
    }
    if (persistState.value === 'saved') return '已自动保存到本机'
    return '还没有需要保存的修改'
  })

  async function load(nextRepository: DraftRepository): Promise<void> {
    repository = nextRepository
    plan.value = (await repository.load()) ?? emptyPlan()
    persistState.value = plan.value.items.length ? 'saved' : 'idle'
    loaded.value = true
  }

  async function reload(): Promise<void> {
    if (!repository) return
    plan.value = (await repository.load()) ?? emptyPlan()
    persistState.value = plan.value.items.length ? 'saved' : 'idle'
  }

  function schedulePersist(): void {
    if (!repository || persistenceSuspended) {
      return
    }
    if (persistTimer) {
      clearTimeout(persistTimer)
    }
    persistState.value = 'pending'
    persistTimer = setTimeout(() => {
      void flushPersist().catch(() => undefined)
    }, 300)
  }

  async function flushPersist(): Promise<void> {
    if (!repository || persistenceSuspended) {
      return
    }
    if (persistTimer) {
      clearTimeout(persistTimer)
      persistTimer = undefined
    }
    if (persistInFlight) {
      await persistInFlight.catch(() => undefined)
      if (persistenceSuspended) return
    }
    persistState.value = 'saving'
    plan.value.updatedAt = new Date().toISOString()
    const operation = repository.save(cloneJson(plan.value))
    persistInFlight = operation
    try {
      await operation
      persistState.value = 'saved'
    } catch (error) {
      persistState.value = 'failed'
      throw error
    } finally {
      if (persistInFlight === operation) persistInFlight = null
    }
  }

  async function retryPersist(): Promise<void> {
    try {
      await flushPersist()
    } catch {
      // The visible failed state remains available for another user retry.
    }
  }

  async function quiescePersistence(): Promise<void> {
    persistenceSuspended = true
    if (persistTimer) {
      clearTimeout(persistTimer)
      persistTimer = undefined
    }
    await persistInFlight?.catch(() => undefined)
  }

  function resumePersistence(): void {
    persistenceSuspended = false
    if (persistState.value === 'pending') schedulePersist()
  }

  function adoptPersistedPlan(nextPlan: DraftPlan): void {
    if (persistTimer) {
      clearTimeout(persistTimer)
      persistTimer = undefined
    }
    plan.value = cloneJson(nextPlan)
  }

  function resetLocalState(keepSuspended = false): void {
    if (persistTimer) {
      clearTimeout(persistTimer)
      persistTimer = undefined
    }
    plan.value = emptyPlan()
    persistState.value = 'idle'
    persistenceSuspended = keepSuspended
  }

  function updatePlanName(name: string): void {
    const normalized = name.trim()
    if (!normalized) return
    plan.value.name = normalized
    schedulePersist()
  }

  function addCandidates(
    candidates: AnalysisCandidate[],
    editedSegmentIds: string[] = [],
    sources: Record<string, SourceSnapshot> = {},
    editedRoleIds: string[] = [],
  ): void {
    const edited = new Set(editedSegmentIds)
    const roleEdited = new Set(editedRoleIds)
    const ordered = [...candidates].sort((left, right) => (
      left.segment.start_seconds - right.segment.start_seconds
      || left.segment.end_seconds - right.segment.end_seconds
    ))
    plan.value.items.push(...ordered.map((candidate) =>
      fromCandidate(
        candidate,
        edited.has(candidate.id),
        sources[candidate.source_id],
        roleEdited.has(candidate.id),
      ),
    ))
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
    if (!item) {
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
    persistState,
    persistMessage,
    load,
    reload,
    flushPersist,
    retryPersist,
    quiescePersistence,
    resumePersistence,
    adoptPersistedPlan,
    resetLocalState,
    updatePlanName,
    addCandidates,
    addManualAction,
    updateValue,
    updateName,
    updateMode,
    move,
    duplicate,
    remove,
  }
})
