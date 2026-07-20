import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import { useAnalysisStore } from '@/stores/analysis'
import VideoAnalysisView from '@/views/VideoAnalysisView.vue'

describe('视频动作分析页', () => {
  afterEach(() => vi.restoreAllMocks())

  it('cancels an owned run on leave even after its visible state becomes failed', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const analysis = useAnalysisStore()
    analysis.activeRunId = 'run-1'
    analysis.status = 'failed'
    vi.spyOn(analysis, 'loadSources').mockResolvedValue()
    const cancel = vi.spyOn(analysis, 'cancel').mockResolvedValue()
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: VideoAnalysisView },
        { path: '/plan', component: { template: '<p>plan</p>' } },
        { path: '/mine', component: { template: '<p>mine</p>' } },
      ],
    })
    await router.push('/')
    await router.isReady()

    const wrapper = mount(VideoAnalysisView, { global: { plugins: [pinia, router] } })
    await flushPromises()
    wrapper.unmount()

    expect(cancel).toHaveBeenCalledTimes(1)
  })
})
