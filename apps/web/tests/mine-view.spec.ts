import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import type { LibraryRepository } from '@/db/library-repository'
import type { DraftPlan, DraftRepository } from '@/domain/types'
import type {
  Preferences,
  SavedPlan,
  TrainingProfile,
  TrainingRecord,
  TrainingSession,
} from '@/domain/training'
import { createQuickExperienceDraftItems } from '@/features/quick-experience/fixture'
import {
  LocalDataCoordinationUnavailableError,
  type LocalDataClearCoordinator,
  type LocalDataClearParticipant,
} from '@/local-data/clear-coordinator'
import { useDraftStore } from '@/stores/draft'
import { useLibraryStore } from '@/stores/library'
import { useLocalDataClearStore } from '@/stores/local-data-clear'
import { useTrainingStore } from '@/stores/training'
import type { TrainingCommand, TrainingEngine, TrainingEngineResult } from '@/training/training-engine'
import MineView from '@/views/MineView.vue'

const session = (): TrainingSession => {
  const item = createQuickExperienceDraftItems()[0]!
  return {
    id: 'current',
    sessionId: 'session-1',
    revision: 1,
    status: 'paused',
    pauseReason: 'user',
    plan: { name: '手臂训练', source: 'draft', sourcePlanId: null, items: [item] },
    currentItemIndex: 0,
    currentSetIndex: 0,
    currentSetActiveMilliseconds: 0,
    activeStartedAt: null,
    restStartedAt: null,
    restEndsAt: null,
    scheduledRestSeconds: null,
    creditedRestMilliseconds: 0,
    progress: [{ itemId: item.id, completedSets: 0, activeMilliseconds: 0, skipped: false }],
    petId: 'hachimi',
    startedAt: '2026-07-21T00:00:00.000Z',
    updatedAt: '2026-07-21T00:01:00.000Z',
  }
}

const endedRecord = (): TrainingRecord => ({
  id: 'session-1',
  outcome: 'ended_early',
  plan: session().plan,
  actions: [],
  activeSeconds: 0,
  creditedRestSeconds: 0,
  trainingDurationSeconds: 0,
  completedActionCount: 0,
  calorie: { value: 0, method: 'generic' },
  petId: 'hachimi',
  startedAt: '2026-07-21T00:00:00.000Z',
  endedAt: '2026-07-21T00:02:00.000Z',
})

class MemoryLibraryRepository implements LibraryRepository {
  plans: SavedPlan[] = []
  records: TrainingRecord[] = []
  openCalls: string[] = []
  clearCalls = 0

  async listPlans() { return structuredClone(this.plans) }
  async listRecords() { return structuredClone(this.records) }
  async loadProfile() { return null }
  async loadPreferences() { return null }
  async saveProfile(profile: Omit<TrainingProfile, 'id' | 'updatedAt'>) {
    return { ...profile, id: 'current' as const, updatedAt: new Date(0).toISOString() }
  }
  async savePreferences(preferences: Omit<Preferences, 'id' | 'updatedAt'>) {
    return { ...preferences, id: 'current' as const, updatedAt: new Date(0).toISOString() }
  }
  async saveCurrentDraftAs(): Promise<never> { throw new Error('not used') }
  async openPlan(planId: string): Promise<DraftPlan> {
    const plan = this.plans.find((candidate) => candidate.id === planId)
    if (!plan) throw new Error('plan not found')
    this.openCalls.push(planId)
    return {
      id: 'current',
      name: plan.name,
      linkedPlanId: plan.id,
      items: structuredClone(plan.items),
      updatedAt: new Date(0).toISOString(),
    }
  }
  async replaceCurrentDraft(): Promise<never> { throw new Error('not used') }
  async clearAllLocalData() { this.clearCalls += 1 }
}

class EndEarlyEngine implements TrainingEngine {
  constructor(private readonly library: MemoryLibraryRepository) {}

  async restore(): Promise<TrainingEngineResult> {
    return { ok: true, session: session(), record: null, events: [] }
  }

  async dispatch(command: TrainingCommand): Promise<TrainingEngineResult> {
    if (command.type !== 'session.end_early') throw new Error('unexpected command')
    const record = endedRecord()
    this.library.records = [record]
    return { ok: true, session: null, record, events: [{ type: 'session.ended_early', recordId: record.id }] }
  }
}

class DeferredDraftRepository implements DraftRepository {
  saveStarted!: () => void
  releaseSave!: () => void
  readonly started = new Promise<void>((resolve) => { this.saveStarted = resolve })
  private readonly released = new Promise<void>((resolve) => { this.releaseSave = resolve })

  async load(): Promise<DraftPlan | undefined> { return undefined }
  async save(): Promise<void> {
    this.saveStarted()
    await this.released
  }
}

class InlineClearCoordinator implements LocalDataClearCoordinator {
  readonly supported = true
  private participant: LocalDataClearParticipant | null = null

  connect(participant: LocalDataClearParticipant): void {
    this.participant = participant
  }

  async clear(clearDatabase: () => Promise<void>): Promise<void> {
    if (!this.participant) throw new Error('not connected')
    await this.participant.prepare('epoch-2')
    try {
      await clearDatabase()
      await this.participant.commit('epoch-2')
    } catch (error) {
      await this.participant.abort('epoch-2')
      throw error
    }
  }

  close(): void {}
}

class UnsupportedClearCoordinator implements LocalDataClearCoordinator {
  readonly supported = false

  connect(): void {}

  async clear(): Promise<void> {
    throw new LocalDataCoordinationUnavailableError()
  }

  close(): void {}
}

const mountMine = async (pinia: ReturnType<typeof createPinia>) => {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<p>home</p>' } },
      { path: '/mine', component: MineView },
      { path: '/plan', component: { template: '<p>plan</p>' } },
      { path: '/training', component: { template: '<p>training</p>' } },
      { path: '/result/:recordId', component: { template: '<p>result</p>' } },
    ],
  })
  await router.push('/mine')
  await router.isReady()
  return { router, wrapper: mount(MineView, { global: { plugins: [pinia, router] } }) }
}

describe('我的训练', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  it('confirms ending the current session, refreshes history, and opens its result', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const repository = new MemoryLibraryRepository()
    await useLibraryStore().load(repository)
    await useTrainingStore().load(new EndEarlyEngine(repository))
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const { router, wrapper } = await mountMine(pinia)
    await flushPromises()

    await wrapper.get('button[aria-label="结束未完成训练"]').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.fullPath).toBe('/result/session-1')
    expect(useLibraryStore().records[0]?.outcome).toBe('ended_early')
  })

  it('puts an unfinished training before quick experience', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const repository = new MemoryLibraryRepository()
    await useLibraryStore().load(repository)
    await useTrainingStore().load(new EndEarlyEngine(repository))
    const { wrapper } = await mountMine(pinia)
    await flushPromises()

    expect(wrapper.text().indexOf('未完成训练')).toBeLessThan(
      wrapper.text().indexOf('快速体验方案'),
    )
  })

  it('does not replace a non-empty draft with a saved plan without confirmation', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const draft = useDraftStore()
    await draft.load({
      load: async () => undefined,
      save: async () => undefined,
    })
    draft.addManualAction({ name: '当前草稿动作', mode: 'reps' })
    const repository = new MemoryLibraryRepository()
    repository.plans = [{
      id: 'plan-a',
      name: '已存方案',
      items: createQuickExperienceDraftItems(),
      createdAt: '2026-07-21T00:00:00.000Z',
      updatedAt: '2026-07-21T00:00:00.000Z',
    }]
    await useLibraryStore().load(repository)
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const { router, wrapper } = await mountMine(pinia)
    await flushPromises()

    await wrapper.findAll('.row-list button')[0]!.trigger('click')
    await flushPromises()

    expect(confirm).toHaveBeenCalledWith('打开这个方案会替换当前草稿，确定继续吗？')
    expect(repository.openCalls).toEqual([])
    expect(draft.items[0]?.name).toBe('当前草稿动作')
    expect(router.currentRoute.value.path).toBe('/mine')

    confirm.mockReturnValue(true)
    await wrapper.findAll('.row-list button')[0]!.trigger('click')
    await flushPromises()

    expect(repository.openCalls).toEqual(['plan-a'])
    expect(draft.plan.linkedPlanId).toBe('plan-a')
    expect(router.currentRoute.value.path).toBe('/plan')
  })

  it('waits for an in-flight draft save before replacing the current draft', async () => {
    vi.useFakeTimers()
    const pinia = createPinia()
    setActivePinia(pinia)
    const draftRepository = new DeferredDraftRepository()
    const draft = useDraftStore()
    await draft.load(draftRepository)
    draft.addManualAction({ name: '即将被替换的动作', mode: 'reps' })
    await vi.advanceTimersByTimeAsync(301)
    await draftRepository.started
    const repository = new MemoryLibraryRepository()
    repository.plans = [{
      id: 'plan-a',
      name: '已存方案',
      items: createQuickExperienceDraftItems(),
      createdAt: '2026-07-21T00:00:00.000Z',
      updatedAt: '2026-07-21T00:00:00.000Z',
    }]
    await useLibraryStore().load(repository)
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const { wrapper } = await mountMine(pinia)
    await flushPromises()

    await wrapper.findAll('.row-list button')[0]!.trigger('click')
    await Promise.resolve()

    expect(repository.openCalls).toEqual([])
    draftRepository.releaseSave()
    await flushPromises()

    expect(repository.openCalls).toEqual(['plan-a'])
    expect(draft.plan.linkedPlanId).toBe('plan-a')
  })

  it('waits for draft persistence to quiesce before clearing all local data', async () => {
    vi.useFakeTimers()
    const pinia = createPinia()
    setActivePinia(pinia)
    const draftRepository = new DeferredDraftRepository()
    const draft = useDraftStore()
    await draft.load(draftRepository)
    draft.addManualAction({ name: '平板支撑', mode: 'duration' })
    await vi.advanceTimersByTimeAsync(301)
    await draftRepository.started
    const libraryRepository = new MemoryLibraryRepository()
    await useLibraryStore().load(libraryRepository)
    useLocalDataClearStore().initialize(new InlineClearCoordinator())
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const { wrapper } = await mountMine(pinia)

    await wrapper.get('button.clear-data').trigger('click')
    await Promise.resolve()
    expect(libraryRepository.clearCalls).toBe(0)

    draftRepository.releaseSave()
    await flushPromises()
    expect(libraryRepository.clearCalls).toBe(1)
    expect(draft.items).toHaveLength(0)
  })

  it('does not claim data was cleared when safe cross-tab coordination is unavailable', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const libraryRepository = new MemoryLibraryRepository()
    await useLibraryStore().load(libraryRepository)
    useLocalDataClearStore().initialize(new UnsupportedClearCoordinator())
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const { wrapper } = await mountMine(pinia)
    await flushPromises()

    await wrapper.get('button.clear-data').trigger('click')
    await flushPromises()

    expect(libraryRepository.clearCalls).toBe(0)
    expect(wrapper.get('.notice').text()).toContain('数据没有清除')
    expect(wrapper.get('.notice').text()).not.toBe('本机训练数据已清除')
  })
})
