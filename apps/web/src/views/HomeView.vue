<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { analysisClient, browserEventStreamFactory } from '@/api/client'
import {
  localMediaAsFile,
  probeVideoDuration,
  SUPPORTED_LOCAL_MEDIA_TYPES,
  validateLocalMediaFile,
} from '@/domain/local-media'
import { useAnalysisStore } from '@/stores/analysis'
import { useDraftStore } from '@/stores/draft'
import { useLibraryStore } from '@/stores/library'
import { useLocalMediaStore } from '@/stores/local-media'

const router = useRouter()
const analysis = useAnalysisStore()
const draft = useDraftStore()
const library = useLibraryStore()
const localMedia = useLocalMediaStore()
const initializing = ref(true)
const importing = ref(false)
const starting = ref(false)
const importError = ref('')

const current = computed(() => localMedia.current)
const previewUrl = computed(() => current.value ? localMedia.urlFor(current.value.sourceId) : null)
const canStart = computed(() => {
  if (!current.value || !analysis.capabilities) return false
  return validateLocalMediaFile(
    localMediaAsFile(current.value),
    current.value.durationSeconds,
    analysis.capabilities,
  ).ok
})

const formatTime = (seconds: number): string => {
  const safe = Number.isFinite(seconds) ? Math.max(0, seconds) : 0
  return `${Math.floor(safe / 60)}:${Math.floor(safe % 60).toString().padStart(2, '0')}`
}

const formatSize = (bytes: number): string => `${(bytes / 1_000_000).toFixed(1)} MB`

const capabilityCopy = computed(() => {
  const capability = analysis.capabilities
  if (analysis.capabilitiesLoading) return '正在读取当前分析能力…'
  if (!capability) return '暂时无法确认本地视频能力'
  if (!capability.local_upload_enabled) return '当前环境暂不支持本地视频分析'
  return `当前支持最长 ${formatTime(capability.local_analysis_max_seconds)} · ${(capability.local_upload_max_bytes / 1_000_000).toFixed(0)} MB`
})

const initialize = async (): Promise<void> => {
  try {
    await analysis.restore({ client: analysisClient, events: browserEventStreamFactory })
    if (analysis.currentSourceKind === 'local' && analysis.currentSourceId) {
      await localMedia.restore({ sourceId: analysis.currentSourceId })
    } else if (!localMedia.current) {
      await localMedia.restore()
    }
    await Promise.all([
      analysis.loadCapabilities(analysisClient),
      analysis.loadSources(analysisClient),
    ])
  } finally {
    initializing.value = false
  }
}

const chooseFile = async (event: Event): Promise<void> => {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file || importing.value) return
  importError.value = ''

  const capability = analysis.capabilities
  if (!capability) {
    importError.value = '还没有读取到分析能力，请稍后重试'
    return
  }
  if (!SUPPORTED_LOCAL_MEDIA_TYPES.includes(file.type as typeof SUPPORTED_LOCAL_MEDIA_TYPES[number])) {
    importError.value = '请选择 MP4、MOV 或 WebM 视频'
    return
  }
  if (file.size > capability.local_upload_max_bytes) {
    importError.value = `这个文件超过当前 ${(capability.local_upload_max_bytes / 1_000_000).toFixed(0)} MB 上限`
    return
  }

  importing.value = true
  try {
    const durationSeconds = await probeVideoDuration(file)
    const validation = validateLocalMediaFile(file, durationSeconds, capability)
    if (!validation.ok) {
      importError.value = validation.message
      return
    }
    if (analysis.isRunning) {
      const shouldReplace = window.confirm('选择新视频会取消当前分析，确定继续吗？')
      if (!shouldReplace) return
      await analysis.cancel(analysisClient).catch(() => undefined)
    } else if (analysis.hasOwnedRun) {
      analysis.clearResult()
    }
    await localMedia.importFile({ file, durationSeconds })
  } catch {
    importError.value = '无法读取这个视频，请换一个文件重试'
  } finally {
    importing.value = false
  }
}

const startLocalAnalysis = async (): Promise<void> => {
  if (!current.value || !canStart.value || starting.value) return
  starting.value = true
  importError.value = ''
  try {
    const request = analysis.start({
      sourceId: current.value.sourceId,
      file: localMediaAsFile(current.value),
      client: analysisClient,
      events: browserEventStreamFactory,
    })
    await router.push('/analysis')
    await request
  } finally {
    starting.value = false
  }
}

const startControlledAnalysis = async (sourceId: string): Promise<void> => {
  if (starting.value) return
  starting.value = true
  try {
    const request = analysis.start({
      sourceId,
      client: analysisClient,
      events: browserEventStreamFactory,
    })
    await router.push('/analysis')
    await request
  } finally {
    starting.value = false
  }
}

onMounted(initialize)
</script>

<template>
  <main class="tp-page home-page">
    <header class="home-hero">
      <div class="brand-row">
        <span class="brand-stamp" aria-hidden="true">TP</span>
        <p class="tp-kicker">TRAINPAL · 训练搭子</p>
      </div>
      <h1 class="tp-title">刷到的动作，<br>变成今天的训练。</h1>
      <p class="tp-lead">选择一条你想练的视频。TrainPal 会拆出动作、保留原片段，再把它整理成可以直接开始的训练。</p>
    </header>

    <section class="import-workspace" aria-labelledby="import-title">
      <div class="workspace-heading">
        <span>01</span>
        <div>
          <p class="tp-kicker">SELECT A VIDEO</p>
          <h2 id="import-title">从一个视频开始</h2>
        </div>
      </div>

      <div v-if="current && previewUrl" class="video-ticket tp-card">
        <video :src="previewUrl" controls preload="metadata" aria-label="已选择的视频预览" />
        <div class="ticket-copy">
          <div>
            <small>本机视频</small>
            <strong>{{ current.fileName }}</strong>
          </div>
          <dl>
            <div><dt>时长</dt><dd>{{ formatTime(current.durationSeconds) }}</dd></div>
            <div><dt>大小</dt><dd>{{ formatSize(current.sizeBytes) }}</dd></div>
          </dl>
        </div>
      </div>

      <label class="file-picker" :class="{ 'file-picker--secondary': current }">
        <input
          class="tp-visually-hidden"
          type="file"
          accept="video/mp4,video/quicktime,video/webm"
          :disabled="initializing || importing"
          @change="chooseFile"
        >
        <span aria-hidden="true">{{ current ? '↻' : '+' }}</span>
        <b>{{ importing ? '正在读取视频…' : current ? '更换视频' : '选择健身视频' }}</b>
      </label>

      <p class="capability-copy">{{ capabilityCopy }} · 原视频保存在当前设备；服务端临时副本只用于本次分析</p>
      <p v-if="importError" class="form-error" role="alert">{{ importError }}</p>

      <button
        v-if="current"
        class="tp-primary-action start-analysis"
        type="button"
        :disabled="!canStart || starting || importing"
        @click="startLocalAnalysis"
      >
        {{ starting ? '正在交给 TrainPal…' : '开始分析' }}
        <span aria-hidden="true">→</span>
      </button>

      <button
        v-if="analysis.capabilitiesError"
        class="tp-quiet-action retry-capability"
        type="button"
        @click="analysis.loadCapabilities(analysisClient)"
      >
        重新读取分析能力
      </button>
    </section>

    <section v-if="draft.items.length || library.plans.length" class="recent-section" aria-labelledby="recent-title">
      <div class="section-line"><span>02</span><h2 id="recent-title">接着上次</h2></div>
      <div class="recent-links">
        <RouterLink v-if="draft.items.length" to="/plan">
          <span><small>当前方案</small><b>{{ draft.plan.name }}</b></span>
          <strong>{{ draft.items.length }} 个动作</strong>
        </RouterLink>
        <RouterLink v-if="library.plans.length" to="/train">
          <span><small>方案库</small><b>已保存的训练</b></span>
          <strong>{{ library.plans.length }} 份</strong>
        </RouterLink>
      </div>
    </section>

    <details v-if="analysis.sources.length" class="controlled-fallback">
      <summary>暂时没有合适视频？使用受控示例</summary>
      <div>
        <p>示例只用于快速体验完整流程，会明确标注为演示内容。</p>
        <button
          v-for="source in analysis.sources.slice(0, 2)"
          :key="source.id"
          class="tp-secondary-action"
          type="button"
          :disabled="starting"
          @click="startControlledAnalysis(source.id)"
        >
          {{ source.title }}
        </button>
      </div>
    </details>
  </main>
</template>

<style scoped>
.home-page { display: grid; align-content: start; gap: 32px; }
.home-hero { display: grid; gap: 18px; padding-top: 8px; }
.brand-row { display: flex; align-items: center; gap: 10px; }
.brand-stamp { display: grid; width: 42px; height: 42px; place-items: center; border: 2px solid var(--tp-ink); border-radius: 50%; color: var(--tp-surface); background: var(--tp-ink); font: 700 15px/1 var(--font-display); letter-spacing: .08em; }
.home-hero .tp-title { max-width: 650px; }
.home-hero .tp-lead { max-width: 590px; }

.import-workspace { display: grid; gap: 14px; }
.workspace-heading { display: flex; align-items: end; gap: 14px; padding-bottom: 14px; border-bottom: 1px solid var(--tp-line); }
.workspace-heading > span,
.section-line > span { color: var(--tp-primary); font: 700 28px/1 var(--font-display); }
.workspace-heading h2,
.section-line h2 { margin: 3px 0 0; color: var(--tp-ink); font: 700 28px/1 var(--font-display), var(--font-cn); }

.video-ticket { overflow: hidden; }
.video-ticket video { display: block; width: 100%; max-height: min(46dvh, 430px); aspect-ratio: 4 / 3; object-fit: contain; background: var(--tp-training-canvas); }
.ticket-copy { display: grid; gap: 13px; padding: 15px 16px; }
.ticket-copy > div { display: grid; gap: 3px; min-width: 0; }
.ticket-copy small { color: var(--tp-primary-readable); font: 700 11px/1 var(--font-display); letter-spacing: .1em; text-transform: uppercase; }
.ticket-copy strong { overflow: hidden; color: var(--tp-ink); font-size: 14px; text-overflow: ellipsis; white-space: nowrap; }
.ticket-copy dl { display: flex; margin: 0; gap: 20px; }
.ticket-copy dl div { display: flex; gap: 6px; }
.ticket-copy dt { color: var(--tp-muted); font-size: 12px; }
.ticket-copy dd { margin: 0; color: var(--tp-ink); font: 700 13px/1.4 var(--font-display); }

.file-picker { display: flex; min-height: 68px; align-items: center; justify-content: center; gap: 12px; border: 1px solid var(--tp-ink); border-radius: var(--tp-radius-md); color: var(--tp-surface); background: var(--tp-ink); }
.file-picker:focus-within { outline: 2px solid var(--tp-focus); outline-offset: 3px; }
.file-picker > span { display: grid; width: 32px; height: 32px; place-items: center; border: 1px solid rgb(255 253 248 / 35%); border-radius: 50%; font-size: 22px; }
.file-picker--secondary { min-height: 50px; border-color: var(--tp-line); color: var(--tp-ink); background: transparent; }
.file-picker--secondary > span { border-color: var(--tp-line); font-size: 18px; }
.capability-copy { margin: 0; color: var(--tp-muted); font-size: 12px; line-height: 1.6; }
.form-error { margin: 0; color: var(--tp-danger); font-size: 13px; line-height: 1.5; }
.start-analysis { width: 100%; min-height: 58px; margin-top: 4px; }
.start-analysis span { margin-left: auto; font-size: 20px; }
.retry-capability { justify-self: center; }

.recent-section { display: grid; gap: 14px; }
.section-line { display: flex; align-items: baseline; gap: 10px; }
.recent-links { display: grid; border-top: 1px solid var(--tp-line); }
.recent-links a { display: flex; min-height: 70px; align-items: center; justify-content: space-between; gap: 12px; border-bottom: 1px solid var(--tp-line); color: var(--tp-ink); text-decoration: none; }
.recent-links a span { display: grid; gap: 4px; }
.recent-links small { color: var(--tp-muted); font-size: 11px; }
.recent-links b { font-size: 14px; }
.recent-links strong { color: var(--tp-primary-readable); font: 700 14px/1 var(--font-display); }

.controlled-fallback { border-top: 1px solid var(--tp-line); padding-top: 18px; color: var(--tp-muted); }
.controlled-fallback summary { min-height: 44px; font-size: 13px; font-weight: 700; }
.controlled-fallback > div { display: grid; gap: 10px; padding: 6px 0 12px; }
.controlled-fallback p { margin: 0; font-size: 12px; line-height: 1.6; }
.controlled-fallback button { justify-content: flex-start; border-radius: 14px; }

@media (min-width: 768px) {
  .home-page { gap: 42px; }
  .ticket-copy { grid-template-columns: minmax(0, 1fr) auto; align-items: center; }
}
</style>
