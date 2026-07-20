import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import type { DraftPlan, DraftRepository } from '@/domain/types'
import { useAnalysisStore } from '@/stores/analysis'
import { useDraftStore } from '@/stores/draft'
import VideoAnalysisView from '@/views/VideoAnalysisView.vue'

class ControllableDraftRepository implements DraftRepository {
  value: DraftPlan | undefined
  saveCount = 0
  shouldFail = false
  saveStarted!: () => void
  releaseSave!: () => void
  readonly started = new Promise<void>((resolve) => { this.saveStarted = resolve })
  private readonly released = new Promise<void>((resolve) => { this.releaseSave = resolve })

  async load(): Promise<DraftPlan | undefined> {
    return structuredClone(this.value)
  }

  async save(plan: DraftPlan): Promise<void> {
    this.saveCount += 1
    this.saveStarted()
    await this.released
    if (this.shouldFail) throw new Error('indexeddb write failed')
    this.value = structuredClone(plan)
  }
}

const showCompletedCandidate = (analysis: ReturnType<typeof useAnalysisStore>): void => {
  analysis.sources = [{
    id: 'video-a',
    title: '来源视频 A',
    media_url: '/api/v1/sources/video-a/media',
    duration_seconds: 60,
    origin_url: null,
  }]
  analysis.status = 'completed'
  analysis.candidates = [{
    id: 'candidate-a',
    name: '拖拽弯举',
    source_id: 'video-a',
    segment: { start_seconds: 41, end_seconds: 51 },
    parameters: {
      mode: 'reps',
      sets: 3,
      reps: 10,
      duration_seconds: null,
      rest_seconds: 60,
    },
    evidence: [{ type: 'visual', start_seconds: 41, end_seconds: 51 }],
    needs_confirmation: false,
  }]
}

describe('视频动作分析页', () => {
  afterEach(() => vi.restoreAllMocks())

  it('shows a retry action when controlled video sources cannot be loaded', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const analysis = useAnalysisStore()
    const loadSources = vi.spyOn(analysis, 'loadSources').mockImplementation(async () => {
      analysis.sources = []
      analysis.sourcesError = '视频暂时没有读取成功，请重试'
    })
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

    const error = wrapper.get('.source-load-error[role="alert"]')
    expect(error.text()).toContain('视频暂时没有读取成功，请重试')
    await error.get('button').trigger('click')
    expect(loadSources).toHaveBeenCalledTimes(2)
  })

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

  it('invalidates a pending create request when the page leaves before a run id exists', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const analysis = useAnalysisStore()
    analysis.status = 'queued'
    analysis.activeRunId = null
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

  it('returns from candidate review to the video so another time point can be chosen', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const analysis = useAnalysisStore()
    analysis.sources = [{
      id: 'video-a',
      title: '来源视频 A',
      media_url: '/api/v1/sources/video-a/media',
      duration_seconds: 60,
      origin_url: null,
    }]
    vi.spyOn(analysis, 'loadSources').mockResolvedValue()
    vi.spyOn(analysis, 'start').mockImplementation(async () => {
      analysis.status = 'completed'
      analysis.candidates = [{
        id: 'candidate-a',
        name: '拖拽弯举',
        source_id: 'video-a',
        segment: { start_seconds: 41, end_seconds: 51 },
        parameters: {
          mode: 'reps',
          sets: 3,
          reps: 10,
          duration_seconds: null,
          rest_seconds: 60,
        },
        evidence: [{ type: 'visual', start_seconds: 41, end_seconds: 51 }],
        needs_confirmation: false,
      }]
    })
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
    const video = wrapper.get<HTMLVideoElement>('video').element
    Object.defineProperty(video, 'paused', { configurable: true, value: false })
    vi.spyOn(video, 'pause').mockImplementation(() => undefined)
    const play = vi.spyOn(video, 'play').mockResolvedValue()

    await wrapper.get('.analyze-button').trigger('click')
    await flushPromises()

    await wrapper.get('button[aria-label="返回视频并重新选择时间点"]').trigger('click')
    await flushPromises()

    expect(analysis.status).toBe('idle')
    expect(analysis.candidates).toEqual([])
    expect(wrapper.find('.candidate-panel').exists()).toBe(false)
    expect(wrapper.get('.analyze-button').text()).toContain('添加动作')
    expect(play).toHaveBeenCalledTimes(1)
  })

  it('adds a candidate only once while its draft save is in flight', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const analysis = useAnalysisStore()
    showCompletedCandidate(analysis)
    vi.spyOn(analysis, 'loadSources').mockResolvedValue()
    const repository = new ControllableDraftRepository()
    const draft = useDraftStore()
    await draft.load(repository)
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
    showCompletedCandidate(analysis)
    await flushPromises()

    const add = wrapper.get('.primary-action')
    await Promise.all([add.trigger('click'), add.trigger('click')])
    await repository.started

    expect(draft.items).toHaveLength(1)
    expect(repository.saveCount).toBe(1)
    expect(wrapper.get('.primary-action').attributes('disabled')).toBeDefined()
    expect(wrapper.get('.primary-action').text()).toContain('正在加入草稿')

    repository.releaseSave()
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/plan')
  })

  it('restores the persisted draft after an add save fails', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const analysis = useAnalysisStore()
    showCompletedCandidate(analysis)
    vi.spyOn(analysis, 'loadSources').mockResolvedValue()
    const repository = new ControllableDraftRepository()
    repository.shouldFail = true
    const draft = useDraftStore()
    await draft.load(repository)
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
    showCompletedCandidate(analysis)
    await flushPromises()

    await wrapper.get('.primary-action').trigger('click')
    await repository.started
    repository.releaseSave()
    await flushPromises()

    expect(router.currentRoute.value.path).toBe('/')
    expect(draft.items).toHaveLength(0)
    expect(wrapper.get('[role="alert"]').text()).toContain('没有加入成功，请重试')
    expect(wrapper.get('.primary-action').attributes('disabled')).toBeUndefined()
  })
})
