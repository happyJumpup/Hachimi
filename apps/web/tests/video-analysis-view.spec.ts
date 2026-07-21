import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import type { DraftPlan, DraftRepository } from '@/domain/types'
import { useAnalysisStore } from '@/stores/analysis'
import { useDraftStore } from '@/stores/draft'
import { useLocalMediaStore } from '@/stores/local-media'
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
    segment_role: 'follow_along',
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

  it('restores a local source as the primary entry and uploads only after the explicit action', async () => {
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
    vi.spyOn(analysis, 'loadSources').mockImplementation(async () => {
      analysis.sources = []
    })
    const start = vi.spyOn(analysis, 'start').mockResolvedValue()
    const file = new File(['local-video'], 'local.mp4', {
      type: 'video/mp4',
      lastModified: 1,
    })
    const record = {
      sourceId: 'local:primary-test',
      blob: file,
      fileName: file.name,
      mimeType: file.type,
      sizeBytes: file.size,
      lastModified: file.lastModified,
      durationSeconds: 30,
      importedAt: '2026-07-21T00:00:00.000Z',
      updatedAt: '2026-07-21T00:00:00.000Z',
    }
    const localMedia = useLocalMediaStore()
    localMedia.current = record
    vi.spyOn(localMedia, 'restore').mockResolvedValue(record)
    vi.spyOn(localMedia, 'urlFor').mockReturnValue('blob:local-primary-test')
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

    expect(wrapper.get('video').attributes('src')).toBe('blob:local-primary-test')
    expect(start).not.toHaveBeenCalled()
    await wrapper.get('.analyze-button').trigger('click')
    await flushPromises()

    expect(start).toHaveBeenCalledWith(expect.objectContaining({
      sourceId: 'local:primary-test',
      file: expect.any(File),
    }))
  })

  it('disables analysis and keeps plan navigation when source media cannot play', async () => {
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

    await wrapper.get('video').trigger('error')

    expect(wrapper.get('.stage-media-error[role="alert"]').text()).toContain('这个视频暂时无法播放')
    expect(wrapper.find('.analyze-button').exists()).toBe(false)
    expect(wrapper.get('.stage-media-error a[href="/plan"]').text()).toBe('去方案草稿')
  })

  it('allows source switching during analysis and cancels the old run', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const analysis = useAnalysisStore()
    analysis.sources = [
      {
        id: 'video-a',
        title: '来源视频 A',
        media_url: '/api/v1/sources/video-a/media',
        duration_seconds: 54,
        origin_url: null,
      },
      {
        id: 'video-b',
        title: '来源视频 B',
        media_url: '/api/v1/sources/video-b/media',
        duration_seconds: 48,
        origin_url: null,
      },
    ]
    vi.spyOn(analysis, 'loadSources').mockResolvedValue()
    const cancel = vi.spyOn(analysis, 'cancel').mockImplementation(async () => {
      analysis.status = 'cancelled'
      analysis.activeRunId = null
    })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
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

    const sourceSelect = wrapper.get('select#source')
    analysis.status = 'running'
    analysis.activeRunId = 'run-a'
    expect(sourceSelect.attributes('disabled')).toBeUndefined()
    await sourceSelect.setValue('video-b')
    await flushPromises()

    expect(cancel).toHaveBeenCalledTimes(1)
    expect(analysis.status).toBe('idle')
    expect(wrapper.get<HTMLVideoElement>('video').attributes('src')).toBe(
      '/api/v1/sources/video-b/media',
    )
  })

  it('uses the shorter browser media duration as the candidate segment limit', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const analysis = useAnalysisStore()
    showCompletedCandidate(analysis)
    analysis.sources[0]!.duration_seconds = 51
    vi.spyOn(analysis, 'loadSources').mockResolvedValue()
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
    analysis.sources[0]!.duration_seconds = 51
    await flushPromises()
    const media = wrapper.get<HTMLVideoElement>('video')
    Object.defineProperty(media.element, 'duration', { configurable: true, value: 50 })

    await media.trigger('loadedmetadata')
    await flushPromises()

    expect(wrapper.get('.panel-footer p').text()).toContain('不超过视频长度')
    expect(wrapper.get('.primary-action').attributes('disabled')).toBeDefined()
  })

  it('disconnects without cancelling an owned run when the page leaves', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const analysis = useAnalysisStore()
    analysis.activeRunId = 'run-1'
    analysis.status = 'failed'
    vi.spyOn(analysis, 'loadSources').mockResolvedValue()
    const cancel = vi.spyOn(analysis, 'cancel').mockResolvedValue()
    const disconnect = vi.spyOn(analysis, 'disconnect')
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

    expect(cancel).not.toHaveBeenCalled()
    expect(disconnect).toHaveBeenCalledTimes(1)
  })

  it('does not cancel a pending create request when the page leaves', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const analysis = useAnalysisStore()
    analysis.status = 'queued'
    analysis.activeRunId = null
    vi.spyOn(analysis, 'loadSources').mockResolvedValue()
    const cancel = vi.spyOn(analysis, 'cancel').mockResolvedValue()
    const disconnect = vi.spyOn(analysis, 'disconnect')
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

    expect(cancel).not.toHaveBeenCalled()
    expect(disconnect).toHaveBeenCalledTimes(1)
  })

  it('returns from candidate review to the video so the whole source can be retried', async () => {
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
    const start = vi.spyOn(analysis, 'start').mockImplementation(async () => {
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
        segment_role: 'follow_along',
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
    expect(start).toHaveBeenCalledWith(expect.not.objectContaining({ triggerSeconds: expect.anything() }))

    await wrapper.get('button[aria-label="返回视频"]').trigger('click')
    await flushPromises()

    expect(analysis.status).toBe('idle')
    expect(analysis.candidates).toEqual([])
    expect(wrapper.find('.candidate-panel').exists()).toBe(false)
    expect(wrapper.get('.analyze-button').text()).toContain('分析视频动作')
    expect(play).toHaveBeenCalledTimes(1)
  })

  it('restores prior playback even when the remote cancel request fails', async () => {
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
    vi.spyOn(analysis, 'start').mockImplementation(async () => { analysis.status = 'running' })
    vi.spyOn(analysis, 'cancel').mockImplementation(async () => {
      analysis.status = 'cancelled'
      throw new Error('delete request failed')
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
    await wrapper.get('.cancel-button').trigger('click')
    await flushPromises()

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
