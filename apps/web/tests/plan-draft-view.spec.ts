import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import type { LibraryRepository } from '@/db/library-repository'
import type { DraftPlan, DraftRepository } from '@/domain/types'
import { useDraftStore } from '@/stores/draft'
import { useLibraryStore } from '@/stores/library'
import { useTrainingStore } from '@/stores/training'
import type { TrainingCommand, TrainingEngine, TrainingEngineResult } from '@/training/training-engine'
import PlanDraftView from '@/views/PlanDraftView.vue'

class RecoverableDraftRepository implements DraftRepository {
  failSave = true
  value: DraftPlan | undefined

  async load(): Promise<DraftPlan | undefined> {
    return this.value
  }

  async save(plan: DraftPlan): Promise<void> {
    if (this.failSave) throw new Error('indexeddb unavailable')
    this.value = structuredClone(plan)
  }
}

const failingSaveAsRepository = (): LibraryRepository => ({
  listPlans: async () => [],
  listRecords: async () => [],
  loadProfile: async () => null,
  loadPreferences: async () => null,
  saveProfile: async () => { throw new Error('not used') },
  savePreferences: async () => { throw new Error('not used') },
  saveCurrentDraftAs: async () => { throw new Error('indexeddb transaction failed') },
  openPlan: async () => { throw new Error('not used') },
  replaceCurrentDraft: async () => { throw new Error('not used') },
  clearAllLocalData: async () => undefined,
})

class StartStorageFailureEngine implements TrainingEngine {
  async restore(): Promise<TrainingEngineResult> {
    return { ok: true, session: null, record: null, events: [] }
  }

  async dispatch(command: TrainingCommand): Promise<TrainingEngineResult> {
    if (command.type !== 'session.create') throw new Error('unexpected command')
    return {
      ok: false,
      code: 'storage_unavailable',
      message: 'internal database details should not leak',
      session: null,
    }
  }
}

describe('方案草稿保存状态', () => {
  afterEach(() => vi.useRealTimers())

  it('shows a retry action instead of claiming a failed automatic save succeeded', async () => {
    vi.useFakeTimers()
    const pinia = createPinia()
    setActivePinia(pinia)
    const repository = new RecoverableDraftRepository()
    const draft = useDraftStore()
    await draft.load(repository)
    draft.addManualAction({ name: '平板支撑', mode: 'duration' })

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/plan', component: PlanDraftView }],
    })
    await router.push('/plan')
    await router.isReady()
    const wrapper = mount(PlanDraftView, { global: { plugins: [pinia, router] } })

    await vi.advanceTimersByTimeAsync(301)
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain('未保存，点击重试')
    repository.failSave = false
    await wrapper.get('button[aria-label="重试保存草稿"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('已自动保存到本机')
    expect(repository.value?.items[0]?.name).toBe('平板支撑')
  })

  it('keeps the save failure visible when deleting the final draft action fails', async () => {
    vi.useFakeTimers()
    const pinia = createPinia()
    setActivePinia(pinia)
    const repository = new RecoverableDraftRepository()
    const draft = useDraftStore()
    await draft.load(repository)
    draft.addManualAction({ name: '平板支撑', mode: 'duration' })
    draft.remove(draft.items[0]!.id)

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/plan', component: PlanDraftView }],
    })
    await router.push('/plan')
    await router.isReady()
    const wrapper = mount(PlanDraftView, { global: { plugins: [pinia, router] } })
    await vi.advanceTimersByTimeAsync(301)
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain('未保存，点击重试')
    expect(wrapper.get('button[aria-label="重试保存草稿"]')).toBeTruthy()
  })

  it('shows a safe retry when the save-as transaction fails after draft flush', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const repository = new RecoverableDraftRepository()
    repository.failSave = false
    const draft = useDraftStore()
    await draft.load(repository)
    draft.addManualAction({ name: '平板支撑', mode: 'duration' })
    await useLibraryStore().load(failingSaveAsRepository())
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/plan', component: PlanDraftView }],
    })
    await router.push('/plan')
    await router.isReady()
    const wrapper = mount(PlanDraftView, { global: { plugins: [pinia, router] } })

    await wrapper.get('section.save-as-panel > button').trigger('click')
    await wrapper.get('input[aria-label="新方案名称"]').setValue('我的训练')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(wrapper.get('.operation-error[role="alert"]').text()).toContain('另存为没有成功')
    expect(wrapper.get('button[aria-label="重试另存为"]')).toBeTruthy()
    expect(wrapper.text()).not.toContain('indexeddb transaction failed')
  })

  it('shows a safe retry when starting training cannot write local data', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const repository = new RecoverableDraftRepository()
    repository.failSave = false
    const draft = useDraftStore()
    await draft.load(repository)
    draft.addManualAction({ name: '平板支撑', mode: 'duration' })
    await useTrainingStore().load(new StartStorageFailureEngine())
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/plan', component: PlanDraftView }],
    })
    await router.push('/plan')
    await router.isReady()
    const wrapper = mount(PlanDraftView, { global: { plugins: [pinia, router] } })

    await wrapper.get('.start-training-panel button').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.path).toBe('/plan')
    expect(wrapper.get('.operation-error[role="alert"]').text()).toContain('没有开始训练')
    expect(wrapper.get('button[aria-label="重试开始训练"]')).toBeTruthy()
    expect(wrapper.text()).not.toContain('internal database details should not leak')
  })
})
