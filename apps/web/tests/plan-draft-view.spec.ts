import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import type { DraftPlan, DraftRepository } from '@/domain/types'
import { useDraftStore } from '@/stores/draft'
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
})
