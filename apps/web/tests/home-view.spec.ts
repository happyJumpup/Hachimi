import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import HomeView from '@/views/HomeView.vue'
import { useAnalysisStore } from '@/stores/analysis'
import { useLocalMediaStore } from '@/stores/local-media'

describe('TrainPal 首页', () => {
  it('keeps upload explicit and starts analysis only from the primary action', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const analysis = useAnalysisStore()
    vi.spyOn(analysis, 'restore').mockResolvedValue()
    vi.spyOn(analysis, 'loadCapabilities').mockImplementation(async () => {
      analysis.capabilities = {
        local_upload_enabled: true,
        local_analysis_max_seconds: 60,
        local_upload_max_bytes: 25_000_000,
      }
    })
    vi.spyOn(analysis, 'loadSources').mockResolvedValue()
    const start = vi.spyOn(analysis, 'start').mockResolvedValue()

    const file = new File(['fixture'], '今天练肩.mp4', {
      type: 'video/mp4',
      lastModified: 7,
    })
    const record = {
      sourceId: 'local:home-fixture',
      blob: file,
      fileName: file.name,
      mimeType: file.type,
      sizeBytes: file.size,
      lastModified: file.lastModified,
      durationSeconds: 38,
      importedAt: '2026-07-22T00:00:00.000Z',
      updatedAt: '2026-07-22T00:00:00.000Z',
    }
    const localMedia = useLocalMediaStore()
    localMedia.current = record
    vi.spyOn(localMedia, 'urlFor').mockReturnValue('blob:home-fixture')

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: HomeView },
        { path: '/analysis', component: { template: '<p>分析页</p>' } },
        { path: '/plan', component: { template: '<p>方案页</p>' } },
        { path: '/train', component: { template: '<p>训练中心</p>' } },
      ],
    })
    await router.push('/')
    await router.isReady()
    const wrapper = mount(HomeView, { global: { plugins: [pinia, router] } })
    await flushPromises()

    expect(wrapper.text()).toContain('刷到的动作')
    expect(wrapper.text()).toContain('原视频保存在当前设备；服务端临时副本只用于本次分析')
    expect(wrapper.get('video').attributes('src')).toBe('blob:home-fixture')
    expect(start).not.toHaveBeenCalled()

    await wrapper.get('.start-analysis').trigger('click')
    await flushPromises()

    expect(start).toHaveBeenCalledWith(expect.objectContaining({
      sourceId: 'local:home-fixture',
      file: expect.any(File),
    }))
    expect(router.currentRoute.value.path).toBe('/analysis')
  })
})
