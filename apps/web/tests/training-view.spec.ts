import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import type { SourceSummary } from '@/domain/types'
import type { TrainingSession } from '@/domain/training'
import { createQuickExperienceDraftItems } from '@/features/quick-experience/fixture'
import { useAnalysisStore } from '@/stores/analysis'
import { useLocalMediaStore } from '@/stores/local-media'
import { useTrainingStore } from '@/stores/training'
import type { TrainingCommand, TrainingEngine, TrainingEngineResult } from '@/training/training-engine'
import TrainingView from '@/views/TrainingView.vue'

const trainingSession = (
  status: TrainingSession['status'],
  currentItemIndex = 0,
): TrainingSession => {
  const baseItems = createQuickExperienceDraftItems()
  const items = [
    baseItems[0]!,
    baseItems[1]!,
    { ...structuredClone(baseItems[0]!), id: 'quick-action-3', name: '第三个动作' },
  ]
  return {
    id: 'current',
    sessionId: 'session-1',
    revision: 1,
    status,
    pauseReason: status === 'paused' ? 'user' : null,
    plan: { name: '三动作训练', source: 'sample', sourcePlanId: null, items },
    currentItemIndex,
    currentSetIndex: 0,
    currentSetActiveMilliseconds: 0,
    activeStartedAt: status === 'active' ? '2026-07-21T00:00:00.000Z' : null,
    restStartedAt: null,
    restEndsAt: null,
    scheduledRestSeconds: null,
    creditedRestMilliseconds: 0,
    progress: items.map((item) => ({
      itemId: item.id,
      completedSets: 0,
      activeMilliseconds: 0,
      skipped: false,
    })),
    petId: 'hachimi',
    startedAt: '2026-07-21T00:00:00.000Z',
    updatedAt: '2026-07-21T00:00:00.000Z',
  }
}

class SessionEngine implements TrainingEngine {
  commands: TrainingCommand[] = []

  constructor(
    private current: TrainingSession,
    private readonly conflict = false,
  ) {}

  async restore(): Promise<TrainingEngineResult> {
    return { ok: true, session: this.current, record: null, events: [] }
  }

  async dispatch(command: TrainingCommand): Promise<TrainingEngineResult> {
    this.commands.push(command)
    if (this.conflict) {
      return {
        ok: false,
        code: 'session_conflict',
        message: '训练状态已在其他页面更新，请继续最新进度',
        session: { ...this.current, revision: this.current.revision + 1 },
      }
    }
    this.current = { ...this.current, status: 'active', revision: this.current.revision + 1 }
    return { ok: true, session: this.current, record: null, events: [] }
  }
}

const source: SourceSummary = {
  id: 'controlled-video',
  title: '受控视频',
  media_url: '/api/v1/sources/controlled-video/media',
  duration_seconds: 60,
  origin_url: null,
}

const mountTraining = async (pinia: ReturnType<typeof createPinia>) => {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/plan', component: { template: '<p>plan</p>' } },
      { path: '/training', component: TrainingView },
    ],
  })
  await router.push('/training')
  await router.isReady()
  const wrapper = mount(TrainingView, { global: { plugins: [pinia, router] } })
  await flushPromises()
  return wrapper
}

describe('训练页合同', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  it('shows the action position and locks the ready CTA to 准备继续', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    await useTrainingStore().load(new SessionEngine(trainingSession('ready_to_continue', 1)))
    useAnalysisStore().sources = [source]
    const wrapper = await mountTraining(pinia)

    expect(wrapper.text()).toContain('动作 2 / 3')
    expect(wrapper.get('button.primary-action').text()).toBe('准备继续')
    wrapper.unmount()
  })

  it('labels a paused in-progress set as 继续训练 instead of restarting it', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    await useTrainingStore().load(new SessionEngine(trainingSession('paused')))
    useAnalysisStore().sources = [source]
    const wrapper = await mountTraining(pinia)

    expect(wrapper.get('button.primary-action').text()).toBe('继续训练')
    wrapper.unmount()
  })

  it('shows a user-entered weight as part of the current set target', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const current = trainingSession('paused')
    current.plan.items[0]!.weightKg = { value: 12.5, source: 'user' }
    await useTrainingStore().load(new SessionEngine(current))
    useAnalysisStore().sources = [source]
    const wrapper = await mountTraining(pinia)

    expect(wrapper.text()).toContain('12.5 kg')
    wrapper.unmount()
  })

  it('shows the original-video link from the immutable action snapshot', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const current = trainingSession('paused')
    current.plan.items[0]!.sourceRef = {
      sourceId: source.id,
      title: source.title,
      originUrl: 'https://www.douyin.com/video/123456',
    }
    current.plan.items[0]!.segment = {
      value: { start_seconds: 41, end_seconds: 51 },
      source: 'video',
    }
    await useTrainingStore().load(new SessionEngine(current))
    useAnalysisStore().sources = [source]
    const wrapper = await mountTraining(pinia)

    const link = wrapper.get('a.original-video-link')
    expect(link.text()).toBe('查看原视频')
    expect(link.attributes()).toMatchObject({
      href: 'https://www.douyin.com/video/123456',
      target: '_blank',
      rel: 'noopener noreferrer',
    })
    wrapper.unmount()
  })

  it('keeps training usable when the reference video cannot play', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const current = trainingSession('paused')
    current.plan.items[0]!.sourceRef = {
      sourceId: source.id,
      title: source.title,
    }
    current.plan.items[0]!.segment = {
      value: { start_seconds: 41, end_seconds: 51 },
      source: 'video',
    }
    await useTrainingStore().load(new SessionEngine(current))
    useAnalysisStore().sources = [source]
    const wrapper = await mountTraining(pinia)

    await wrapper.get('video').trigger('error')

    expect(wrapper.get('.media-placeholder[role="status"]').text()).toContain('参考视频暂时不可用')
    expect(wrapper.get('button.primary-action').text()).toBe('继续训练')
    wrapper.unmount()
  })

  it('plays a restored local Blob and still loads controlled sources after advancing', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const current = trainingSession('paused')
    current.plan.items[0]!.sourceRef = {
      sourceId: 'local:training-source',
      kind: 'local',
      title: '本地训练视频',
      localMedia: {
        fileName: 'training.mp4',
        mimeType: 'video/mp4',
        sizeBytes: 12,
        lastModified: 1,
        durationSeconds: 60,
      },
    }
    current.plan.items[0]!.segment = {
      value: { start_seconds: 4, end_seconds: 12 },
      source: 'video',
    }
    current.plan.items[1]!.sourceRef = { sourceId: source.id, title: source.title }
    current.plan.items[1]!.segment = {
      value: { start_seconds: 20, end_seconds: 30 },
      source: 'video',
    }
    const training = useTrainingStore()
    await training.load(new SessionEngine(current))
    const localMedia = useLocalMediaStore()
    vi.spyOn(localMedia, 'resolve').mockResolvedValue('blob:restored-local-video')
    const analysis = useAnalysisStore()
    const loadSources = vi.spyOn(analysis, 'loadSources').mockImplementation(async () => {
      analysis.sources = [source]
    })
    const wrapper = await mountTraining(pinia)

    expect(wrapper.get('video').attributes('src')).toBe('blob:restored-local-video')
    expect(wrapper.text()).toContain('本地片段循环')
    expect(loadSources).not.toHaveBeenCalled()

    training.session = { ...current, currentItemIndex: 1 }
    await flushPromises()

    expect(loadSources).toHaveBeenCalledTimes(1)
    expect(wrapper.get('video').attributes('src')).toBe(source.media_url)
    wrapper.unmount()
  })

  it('restarts an active segment when the real media ends before its manifest endpoint', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const current = trainingSession('active')
    current.plan.items[0]!.sourceRef = {
      sourceId: source.id,
      title: source.title,
    }
    current.plan.items[0]!.segment = {
      value: { start_seconds: 41, end_seconds: 51 },
      source: 'video',
    }
    await useTrainingStore().load(new SessionEngine(current))
    useAnalysisStore().sources = [{ ...source, duration_seconds: 51 }]
    const play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue()
    const wrapper = await mountTraining(pinia)
    play.mockClear()
    const media = wrapper.get<HTMLVideoElement>('video')
    Object.defineProperty(media.element, 'currentTime', {
      configurable: true,
      writable: true,
      value: 50,
    })
    await media.trigger('ended')
    await flushPromises()

    expect(media.element.currentTime).toBe(41)
    expect(play).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('visibly pauses and stops ticking after another tab wins a revision conflict', async () => {
    vi.useFakeTimers()
    const pinia = createPinia()
    setActivePinia(pinia)
    const engine = new SessionEngine(trainingSession('active'), true)
    await useTrainingStore().load(engine)
    useAnalysisStore().sources = [source]
    const wrapper = await mountTraining(pinia)

    await wrapper.get('button.primary-action').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('此页面已暂停')
    expect(wrapper.get('button.primary-action').attributes('disabled')).toBeDefined()

    await vi.advanceTimersByTimeAsync(1_100)
    await flushPromises()
    expect(engine.commands).toHaveLength(1)
    wrapper.unmount()
  })
})
