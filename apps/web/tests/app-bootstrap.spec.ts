import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import App from '@/App.vue'
import { useAppBootstrapStore } from '@/stores/app-bootstrap'
import { useTrainingStore } from '@/stores/training'
import type { TrainingCommand, TrainingEngine, TrainingEngineResult } from '@/training/training-engine'

const restoredSession = (): NonNullable<TrainingEngineResult['session']> => ({
  id: 'current',
  sessionId: 'session-1',
  revision: 1,
  status: 'paused',
  pauseReason: 'recovered',
  plan: { name: '手臂训练', source: 'draft', sourcePlanId: null, items: [] },
  currentItemIndex: 0,
  currentSetIndex: 0,
  currentSetActiveMilliseconds: 0,
  activeStartedAt: null,
  restStartedAt: null,
  restEndsAt: null,
  scheduledRestSeconds: null,
  creditedRestMilliseconds: 0,
  progress: [],
  petId: 'hachimi',
  startedAt: '2026-07-21T00:00:00.000Z',
  updatedAt: '2026-07-21T00:01:00.000Z',
})

class RestoredTrainingEngine implements TrainingEngine {
  async restore(): Promise<TrainingEngineResult> {
    return { ok: true, session: restoredSession(), record: null, events: [] }
  }

  async dispatch(_command: TrainingCommand): Promise<TrainingEngineResult> {
    throw new Error('not used')
  }
}

class UnavailableTrainingEngine implements TrainingEngine {
  async restore(): Promise<TrainingEngineResult> {
    return {
      ok: false,
      code: 'storage_unavailable',
      message: '本机训练数据暂时无法读取',
      session: null,
    }
  }

  async dispatch(_command: TrainingCommand): Promise<TrainingEngineResult> {
    throw new Error('not used')
  }
}

describe('应用本机数据启动壳', () => {
  it('mounts a recoverable error shell when IndexedDB bootstrap fails', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', component: { template: '<p>首页已就绪</p>' } }],
    })
    await router.push('/')
    await router.isReady()
    const wrapper = mount(App, { global: { plugins: [pinia, router] } })
    const bootstrap = useAppBootstrapStore()
    let attempts = 0

    await bootstrap.initialize(async () => {
      attempts += 1
      if (attempts === 1) throw new Error('indexeddb unavailable')
    })

    expect(wrapper.get('[role="alert"]').text()).toContain('本机训练数据暂时无法读取')
    expect(wrapper.text()).not.toContain('indexeddb unavailable')

    await wrapper.get('button').trigger('click')
    await flushPromises()

    expect(attempts).toBe(2)
    expect(wrapper.text()).toContain('首页已就绪')
  })

  it('treats a failed training-session restore as a bootstrap failure', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', component: { template: '<p>首页已就绪</p>' } }],
    })
    await router.push('/')
    await router.isReady()
    const wrapper = mount(App, { global: { plugins: [pinia, router] } })

    await useAppBootstrapStore().initialize(async () => {
      await useTrainingStore().load(new UnavailableTrainingEngine())
    })

    expect(wrapper.get('[role="alert"]').text()).toContain('本机训练数据暂时无法读取')
    expect(wrapper.text()).not.toContain('首页已就绪')
  })

  it('does not cover the plan primary action with the global continue entry', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', name: 'home', component: { template: '<p>视频页</p>' } },
        { path: '/plan', name: 'plan', component: { template: '<p>方案页</p>' } },
        { path: '/mine', name: 'mine', component: { template: '<p>我的训练</p>' } },
      ],
    })
    await router.push('/plan')
    await router.isReady()
    const wrapper = mount(App, { global: { plugins: [pinia, router] } })
    await useAppBootstrapStore().initialize(async () => {
      await useTrainingStore().load(new RestoredTrainingEngine())
    })
    await flushPromises()

    expect(wrapper.find('.global-training-entry').exists()).toBe(false)

    await router.push('/mine')
    await flushPromises()

    expect(wrapper.find('.global-training-entry').exists()).toBe(false)

    await router.push('/')
    await flushPromises()

    expect(wrapper.get('.global-training-entry').text()).toContain('继续训练')
  })
})
