import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import type { LibraryRepository } from '@/db/library-repository'
import type { DraftPlan } from '@/domain/types'
import type { Preferences, TrainingProfile } from '@/domain/training'
import { useDraftStore } from '@/stores/draft'
import { useLibraryStore } from '@/stores/library'
import PersonalizeView from '@/views/PersonalizeView.vue'
import TrainHubView from '@/views/TrainHubView.vue'

const emptyLibrary = (): LibraryRepository => ({
  listPlans: async () => [],
  listRecords: async () => [],
  loadProfile: async () => null,
  loadPreferences: async () => null,
  saveProfile: async (profile: Omit<TrainingProfile, 'id' | 'updatedAt'>) => ({
    ...profile,
    id: 'current',
    updatedAt: new Date(0).toISOString(),
  }),
  savePreferences: async (preferences: Omit<Preferences, 'id' | 'updatedAt'>) => ({
    ...preferences,
    id: 'current',
    updatedAt: new Date(0).toISOString(),
  }),
  saveCurrentDraftAs: async () => { throw new Error('not used') },
  openPlan: async () => { throw new Error('not used') },
  deletePlan: async () => null,
  replaceCurrentDraft: async ({ name, items }): Promise<DraftPlan> => ({
    id: 'current',
    name,
    linkedPlanId: null,
    items: structuredClone(items),
    updatedAt: new Date(0).toISOString(),
  }),
  clearAllLocalData: async () => undefined,
})

const mountAt = async (
  component: typeof PersonalizeView | typeof TrainHubView,
  path: string,
) => {
  const pinia = createPinia()
  setActivePinia(pinia)
  await useDraftStore().load({ load: async () => undefined, save: async () => undefined })
  await useLibraryStore().load(emptyLibrary())
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/personalize', component: PersonalizeView },
      { path: '/train', component: TrainHubView },
      { path: '/plan', component: { template: '<p>plan</p>' } },
      { path: '/mine', component: { template: '<p>mine</p>' } },
      { path: '/', component: { template: '<p>home</p>' } },
    ],
  })
  await router.push(path)
  await router.isReady()
  return mount(component, { global: { plugins: [pinia, router] } })
}

describe('TrainPal 旅程页面', () => {
  it('keeps personalization honest while GYMTI and coach assets are pending', async () => {
    const wrapper = await mountAt(PersonalizeView, '/personalize')

    expect(wrapper.text()).toContain('这版不生成假个性化结果')
    expect(wrapper.text()).toContain('目标导向问卷正在定稿')
    expect(wrapper.text()).toContain('形象制作中')
    expect(wrapper.text()).toContain('你的手动调整始终优先')
    expect(wrapper.findAll('.tp-primary-action')).toHaveLength(1)
  })

  it('keeps the training hub focused on the next training action without duplicating records', async () => {
    const wrapper = await mountAt(TrainHubView, '/train')

    expect(wrapper.text()).toContain('先准备一场想练的训练')
    expect(wrapper.text()).not.toContain('训练记录')
    expect(wrapper.findAll('.tp-primary-action')).toHaveLength(1)
  })
})
