<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import AccessStatus from '@/components/AccessStatus.vue'
import CandidateReviewPanel from '@/components/CandidateReviewPanel.vue'
import { analysisClient, browserEventStreamFactory } from '@/api/client'
import {
  localMediaAsFile,
  fingerprintMatches,
  probeVideoDuration,
  SUPPORTED_LOCAL_MEDIA_TYPES,
  validateLocalMediaFile,
} from '@/domain/local-media'
import type {
  AnalysisCandidate,
  CoverageGap,
  DraftPlan,
  LocalMediaFingerprint,
  Segment,
} from '@/domain/types'
import { useAccessStore } from '@/stores/access'
import { useAnalysisStore } from '@/stores/analysis'
import { useDraftStore } from '@/stores/draft'
import { useLocalMediaStore } from '@/stores/local-media'

const router = useRouter()
const access = useAccessStore()
const analysis = useAnalysisStore()
const draft = useDraftStore()
const localMedia = useLocalMediaStore()
const video = ref<HTMLVideoElement>()
const selectedSourceId = ref('')
const sourceKind = ref<'controlled' | 'local'>('controlled')
const currentSeconds = ref(0)
const durationSeconds = ref(0)
const previewEnd = ref<number | null>(null)
const mediaLoadFailed = ref(false)
const addingCandidates = ref(false)
const addCandidatesError = ref('')
const localImporting = ref(false)
const localImportError = ref('')
const initializing = ref(true)
const localGateRechecked = ref(false)
let resumeAfterCancel = false

const selectedControlledSource = computed(() =>
  analysis.sources.find((source) => source.id === selectedSourceId.value),
)
const selectedLocalRecord = computed(() => (
  sourceKind.value === 'local' && localMedia.current?.sourceId === selectedSourceId.value
    ? localMedia.current
    : null
))
const selectedMediaUrl = computed(() => sourceKind.value === 'local'
  ? localMedia.urlFor(selectedSourceId.value)
  : selectedControlledSource.value?.media_url ?? null)
const selectedTitle = computed(() => sourceKind.value === 'local'
  ? selectedLocalRecord.value?.fileName ?? '本地视频待重新选择'
  : selectedControlledSource.value?.title ?? '快速体验视频')
const configuredDuration = computed(() => sourceKind.value === 'local'
  ? selectedLocalRecord.value?.durationSeconds
  : selectedControlledSource.value?.duration_seconds)
const segmentEndLimit = computed(() => {
  const configured = configuredDuration.value
  if (configured === undefined) return undefined
  return durationSeconds.value > 0
    ? Math.min(configured, durationSeconds.value)
    : configured
})
const hasSelectedMedia = computed(() => Boolean(selectedSourceId.value && selectedMediaUrl.value))
const localSourcePassesCurrentGate = computed(() => {
  if (sourceKind.value !== 'local') return true
  const record = selectedLocalRecord.value
  const capabilities = analysis.capabilities
  if (!record || !capabilities) return false
  return validateLocalMediaFile(
    localMediaAsFile(record),
    record.durationSeconds,
    capabilities,
  ).ok
})
const progressTotalSeconds = computed(() => analysis.gapRetrying
  ? analysis.gapRetrying.end_seconds - analysis.gapRetrying.start_seconds
  : analysis.sourceDurationSeconds || configuredDuration.value || 0)
const localLimitCopy = computed(() => {
  const current = analysis.capabilities
  if (!current) return analysis.capabilitiesLoading ? '正在读取当前环境能力…' : '当前本地分析能力尚未确认'
  if (!current.local_upload_enabled) return '当前环境暂不支持本地视频分析'
  const duration = formatTime(current.local_analysis_max_seconds)
  const megabytes = (current.local_upload_max_bytes / 1_000_000).toFixed(
    current.local_upload_max_bytes % 1_000_000 === 0 ? 0 : 1,
  )
  return `当前上限 ${duration} · ${megabytes} MB · 长视频能力尚未完成验证`
})

const formatTime = (seconds: number): string => {
  const safe = Number.isFinite(seconds) ? Math.max(0, seconds) : 0
  return `${Math.floor(safe / 60)}:${Math.floor(safe % 60).toString().padStart(2, '0')}`
}

const loadSources = async (): Promise<void> => {
  await analysis.loadSources(analysisClient)
  if (
    sourceKind.value === 'controlled'
    && !analysis.hasOwnedRun
    && !analysis.sources.some((source) => source.id === selectedSourceId.value)
  ) {
    selectedSourceId.value = analysis.sources[0]?.id ?? ''
  }
}

const resetMediaState = (): void => {
  addCandidatesError.value = ''
  currentSeconds.value = 0
  durationSeconds.value = 0
  previewEnd.value = null
  mediaLoadFailed.value = false
}

const abandonAnalysisForSourceChange = async (): Promise<boolean> => {
  if (analysis.isRunning) {
    if (!window.confirm('更换视频会取消当前分析，确定继续吗？')) return false
    await analysis.cancel(analysisClient).catch(() => undefined)
    analysis.clearResult()
  } else {
    analysis.clearResult()
  }
  return true
}

const selectControlledSource = async (event: Event): Promise<void> => {
  const target = event.target as HTMLSelectElement
  if (initializing.value) return
  const nextSourceId = target.value
  if (!(await abandonAnalysisForSourceChange())) {
    target.value = sourceKind.value === 'controlled' ? selectedSourceId.value : ''
    return
  }
  sourceKind.value = 'controlled'
  selectedSourceId.value = nextSourceId
  resetMediaState()
  video.value?.load()
}

const importLocalFile = async (event: Event): Promise<void> => {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file || localImporting.value || initializing.value) return
  localGateRechecked.value = false
  localImportError.value = ''
  const capabilities = analysis.capabilities
  if (!capabilities) {
    localImportError.value = '当前本地分析能力尚未确认，请重试读取'
    return
  }
  if (!SUPPORTED_LOCAL_MEDIA_TYPES.includes(file.type as typeof SUPPORTED_LOCAL_MEDIA_TYPES[number])) {
    localImportError.value = '请选择 MP4、MOV 或 WebM 视频'
    return
  }
  if (file.size > capabilities.local_upload_max_bytes) {
    localImportError.value = `这个文件超过当前环境的 ${(capabilities.local_upload_max_bytes / 1_000_000).toFixed(0)} MB 上限`
    return
  }
  localImporting.value = true
  try {
    const duration = await probeVideoDuration(file)
    const validation = validateLocalMediaFile(file, duration, capabilities)
    if (!validation.ok) {
      localImportError.value = validation.message
      return
    }
    const missingFingerprint = sourceKind.value === 'local' && selectedSourceId.value
      ? draft.items.find((item) => item.sourceRef?.sourceId === selectedSourceId.value)?.sourceRef?.localMedia
      : undefined
    const rebindsMissingSource = Boolean(
      sourceKind.value === 'local' && selectedSourceId.value && !selectedLocalRecord.value,
    )
    let sourceId: string | undefined
    if (rebindsMissingSource) {
      const matches = missingFingerprint
        ? fingerprintMatches(missingFingerprint, file, duration)
        : false
      if (!matches) {
        if (!window.confirm('文件信息与原视频不一致或无法核对。继续会清除旧视频的分析恢复状态，确认替换吗？')) {
          localImportError.value = '没有替换原视频；动作与训练数据保持不变'
          return
        }
        if (analysis.hasOwnedRun && analysis.currentSourceId === selectedSourceId.value) {
          if (analysis.isRunning) await analysis.cancel(analysisClient).catch(() => undefined)
          analysis.clearResult()
        }
      }
      sourceId = selectedSourceId.value
    }
    if (!rebindsMissingSource && !(await abandonAnalysisForSourceChange())) return
    const record = await localMedia.importFile({ file, durationSeconds: duration, sourceId })
    sourceKind.value = 'local'
    selectedSourceId.value = record.sourceId
    resetMediaState()
  } catch {
    localImportError.value = '无法读取这个视频，请重新选择'
  } finally {
    localImporting.value = false
  }
}

const initialize = async (): Promise<void> => {
  try {
    await analysis.restore({ client: analysisClient, events: browserEventStreamFactory })
    if (analysis.currentSourceKind === 'local' && analysis.currentSourceId) {
      sourceKind.value = 'local'
      selectedSourceId.value = analysis.currentSourceId
      await localMedia.restore({ sourceId: analysis.currentSourceId })
    } else if (analysis.currentSourceKind === 'controlled' && analysis.currentSourceId) {
      sourceKind.value = 'controlled'
      selectedSourceId.value = analysis.currentSourceId
    }

    await Promise.all([
      analysis.loadCapabilities(analysisClient),
      loadSources(),
    ])

    if (!analysis.currentSourceId) {
      const restored = await localMedia.restore()
      if (restored) {
        sourceKind.value = 'local'
        selectedSourceId.value = restored.sourceId
      } else {
        sourceKind.value = 'controlled'
        selectedSourceId.value = analysis.sources[0]?.id ?? ''
      }
    }
  } finally {
    initializing.value = false
  }
}

const retryCapabilities = async (): Promise<void> => {
  await analysis.loadCapabilities(analysisClient)
  if (analysis.failureKind === 'local_gate') localGateRechecked.value = true
}
const retryRestore = (): Promise<void> => analysis.restore({
  client: analysisClient,
  events: browserEventStreamFactory,
})

onMounted(initialize)

onBeforeUnmount(() => {
  analysis.disconnect()
})

const syncTime = (): void => {
  if (!video.value) return
  currentSeconds.value = video.value.currentTime
  durationSeconds.value = video.value.duration || configuredDuration.value || 0
  if (previewEnd.value !== null && video.value.currentTime >= previewEnd.value) {
    video.value.pause()
    previewEnd.value = null
  }
}

const startAnalysis = async (): Promise<void> => {
  if (
    !selectedSourceId.value
    || initializing.value
    || analysis.isRunning
    || analysis.restoring
    || (analysis.hasOwnedRun && Boolean(analysis.restoreError))
    || mediaLoadFailed.value
    || !selectedMediaUrl.value
  ) return
  if (sourceKind.value === 'local') {
    const record = selectedLocalRecord.value
    const capabilities = analysis.capabilities
    if (!record || !capabilities) {
      localImportError.value = '当前本地分析能力尚未确认，请重试读取'
      return
    }
    const validation = validateLocalMediaFile(
      localMediaAsFile(record),
      record.durationSeconds,
      capabilities,
    )
    if (!validation.ok) {
      localImportError.value = validation.message
      return
    }
  }
  resumeAfterCancel = Boolean(video.value && !video.value.paused)
  localGateRechecked.value = false
  video.value?.pause()
  previewEnd.value = null
  const file = sourceKind.value === 'local' && selectedLocalRecord.value
    ? localMediaAsFile(selectedLocalRecord.value)
    : undefined
  await analysis.start({
    sourceId: selectedSourceId.value,
    file,
    client: analysisClient,
    events: browserEventStreamFactory,
  })
  if (analysis.failureKind === 'capacity') await access.load()
}

const retryGap = async (gap: CoverageGap): Promise<void> => {
  const record = selectedLocalRecord.value
  if (!record) {
    localImportError.value = '本地视频已不可用，请重新选择后再重试这段'
    return
  }
  video.value?.pause()
  await analysis.retryGap({
    gap,
    file: localMediaAsFile(record),
    client: analysisClient,
    events: browserEventStreamFactory,
  })
}

const cancelAnalysis = async (): Promise<void> => {
  try {
    await analysis.cancel(analysisClient)
  } catch {
    // Local cancellation is already applied by the store; DELETE is best effort.
  } finally {
    if (resumeAfterCancel) await video.value?.play().catch(() => undefined)
    resumeAfterCancel = false
  }
}

const preview = async (segment: Segment): Promise<void> => {
  if (!video.value) return
  video.value.currentTime = segment.start_seconds
  previewEnd.value = segment.end_seconds
  await video.value.play().catch(() => undefined)
}

const addCandidates = async (
  candidates: AnalysisCandidate[],
  editedSegmentIds: string[],
  editedRoleIds: string[],
): Promise<void> => {
  if (addingCandidates.value) return
  addingCandidates.value = true
  addCandidatesError.value = ''
  const previousDraft = JSON.parse(JSON.stringify(draft.plan)) as DraftPlan
  const sourceSnapshots: Record<string, {
    title: string
    origin_url: string | null
    kind?: 'controlled' | 'local'
    localMedia?: LocalMediaFingerprint
  }> = Object.fromEntries(
    analysis.sources.map((source) => [source.id, {
      title: source.title,
      origin_url: source.origin_url ?? null,
      kind: 'controlled' as const,
    }]),
  )
  if (selectedLocalRecord.value) {
    const record = selectedLocalRecord.value
    sourceSnapshots[record.sourceId] = {
      title: record.fileName,
      origin_url: null,
      kind: 'local',
      localMedia: {
        fileName: record.fileName,
        mimeType: record.mimeType,
        sizeBytes: record.sizeBytes,
        lastModified: record.lastModified,
        durationSeconds: record.durationSeconds,
      },
    }
  }
  try {
    draft.addCandidates(candidates, editedSegmentIds, sourceSnapshots, editedRoleIds)
    await draft.flushPersist()
    analysis.clearResult()
    await router.push('/plan')
  } catch {
    try {
      await draft.reload()
    } catch {
      draft.adoptPersistedPlan(previousDraft)
    }
    addCandidatesError.value = '没有加入成功，请重试'
  } finally {
    addingCandidates.value = false
  }
}

const returnToVideo = async (): Promise<void> => {
  previewEnd.value = null
  addCandidatesError.value = ''
  analysis.clearResult()
  if (resumeAfterCancel) await video.value?.play().catch(() => undefined)
  resumeAfterCancel = false
}
</script>

<template>
  <main class="video-page">
    <div class="ambient-grid" aria-hidden="true" />
    <header class="topbar">
      <div class="brand-lockup">
        <span class="brand-mark">H</span>
        <div>
          <strong>哈基米练臂力动</strong>
          <small>HACHIMI TRAINING LAB</small>
        </div>
      </div>
      <nav class="top-actions" aria-label="训练导航">
        <RouterLink class="mine-link" to="/mine">我的训练</RouterLink>
        <RouterLink class="draft-link" to="/plan">
          <span>方案草稿</span>
          <b>{{ draft.items.length }}</b>
        </RouterLink>
      </nav>
    </header>

    <AccessStatus />

    <section class="source-entry" aria-labelledby="local-source-title">
      <div>
        <p class="source-entry-eyebrow">LOCAL VIDEO</p>
        <h1 id="local-source-title">选择本地视频</h1>
        <p>{{ localLimitCopy }}</p>
        <p class="rights-note">请选择你有权使用的视频；不会读取平台登录态或解析分享链接。</p>
      </div>
      <label
        class="local-file-button"
        :class="{ disabled: initializing || !analysis.capabilities?.local_upload_enabled || localImporting }"
      >
        <input
          type="file"
          :accept="SUPPORTED_LOCAL_MEDIA_TYPES.join(',')"
          :disabled="initializing || !analysis.capabilities?.local_upload_enabled || localImporting"
          @change="importLocalFile"
        />
        {{ localImporting ? '正在读取视频…' : selectedLocalRecord ? '更换本地视频' : '选择本地视频' }}
      </label>
      <p v-if="localImportError" class="local-import-error" role="alert">{{ localImportError }}</p>
      <button
        v-if="analysis.capabilitiesError"
        type="button"
        class="capability-retry"
        :disabled="analysis.capabilitiesLoading"
        @click="retryCapabilities"
      >
        {{ analysis.capabilitiesLoading ? '正在重新读取…' : '重新读取本地能力' }}
      </button>
      <p
        v-if="selectedLocalRecord && localMedia.storageMessage"
        class="local-storage-note"
        :class="localMedia.storageStatus"
      >
        {{ localMedia.storageMessage }}
      </p>
      <details class="controlled-fallback">
        <summary>快速体验 / 本地导入不可用时使用</summary>
        <div v-if="analysis.sourcesLoading">正在读取快速体验视频…</div>
        <div v-else-if="analysis.sourcesError && !analysis.sources.length" class="source-load-error" role="alert">
          <span>{{ analysis.sourcesError }}</span>
          <button type="button" :disabled="analysis.sourcesLoading" @click="loadSources">重新读取视频</button>
        </div>
        <label v-else for="source">
          <span>受控视频</span>
          <select
            id="source"
            :value="sourceKind === 'controlled' ? selectedSourceId : ''"
            :disabled="initializing"
            @change="selectControlledSource"
          >
            <option v-for="source in analysis.sources" :key="source.id" :value="source.id">
              {{ source.title }}
            </option>
          </select>
        </label>
      </details>
    </section>

    <section v-if="initializing || analysis.restoring || analysis.restoreError" class="recovery-status" aria-live="polite">
      <template v-if="initializing">
        <strong>正在检查上次分析</strong>
        <span>会先读取服务端真实状态，再开放新的分析请求。</span>
      </template>
      <template v-else-if="analysis.restoring">
        <strong>正在恢复这次分析</strong>
        <span>会先读取服务端真实状态，不会重新上传或创建请求。</span>
      </template>
      <template v-else>
        <strong>{{ analysis.restoreError }}</strong>
        <button v-if="analysis.hasOwnedRun" type="button" @click="retryRestore">重试恢复</button>
        <button
          v-else-if="hasSelectedMedia"
          type="button"
          :disabled="!localSourcePassesCurrentGate"
          @click="startAnalysis"
        >
          重新分析
        </button>
        <span v-else>请重新选择原视频后再分析。</span>
      </template>
    </section>

    <section class="video-stage" :class="{ 'has-review': analysis.status === 'completed' && (analysis.candidates.length || analysis.coverageGaps.length) }">
      <div class="source-rail">
        <span class="live-dot" :class="{ local: sourceKind === 'local' }" />
        <span>{{ sourceKind === 'local' ? '本地来源' : '快速体验' }}</span>
        <strong>{{ selectedTitle }}</strong>
      </div>

      <div v-if="!hasSelectedMedia" class="stage-empty">
        <span class="empty-code">NO SOURCE</span>
        <h1>{{ sourceKind === 'local' && selectedSourceId ? '本地视频需要重新选择' : '先选择一条视频' }}</h1>
        <p>{{ sourceKind === 'local' && selectedSourceId ? '动作与训练控制仍会保留；重新选择原文件后恢复播放。' : '优先选择本地视频，也可以展开快速体验。' }}</p>
      </div>
      <template v-else>
        <video
          ref="video"
          class="source-video"
          :src="selectedMediaUrl ?? undefined"
          playsinline
          controls
          preload="metadata"
          @loadedmetadata="syncTime"
          @timeupdate="syncTime"
          @error="mediaLoadFailed = true"
        />

        <div v-if="mediaLoadFailed" class="stage-media-error" role="alert">
          <span class="empty-code">VIDEO UNAVAILABLE</span>
          <strong>这个视频暂时无法播放</strong>
          <small>可以切换视频，或继续编辑已有方案。</small>
          <div>
            <RouterLink to="/plan">去方案草稿</RouterLink>
            <RouterLink to="/mine">我的训练</RouterLink>
          </div>
        </div>

        <div class="video-vignette" aria-hidden="true" />
        <div class="time-readout" aria-live="polite">
          <span>{{ formatTime(currentSeconds) }}</span>
          <i />
          <small>{{ formatTime(durationSeconds) }}</small>
        </div>

        <div v-if="analysis.isRunning" class="analysis-overlay" aria-live="polite">
          <div class="scanner" aria-hidden="true" />
          <div class="analysis-status">
            <span class="pulse-ring" />
            <p>{{ analysis.stageLabel }}</p>
            <small v-if="analysis.gapRetrying">
              正在重试 {{ formatTime(analysis.gapRetrying.start_seconds) }}—{{ formatTime(analysis.gapRetrying.end_seconds) }}
            </small>
            <small v-else>正在分析整条视频中的动作</small>
            <strong class="progress-copy">
              已处理 {{ formatTime(analysis.processedSeconds) }} / {{ formatTime(progressTotalSeconds) }}
            </strong>
            <small>暂时发现 {{ analysis.discoveredCandidateCount }} 条动作线索 · 离开后仍可回来查看</small>
          </div>
          <button type="button" class="cancel-button" @click="cancelAnalysis">取消</button>
        </div>

        <div v-if="analysis.status === 'failed' && analysis.failureKind === 'capacity'" class="result-toast busy-toast">
          <div>
            <strong>实时 AI 名额正在使用</strong>
            <span>{{ analysis.retryAfterSeconds ? `约 ${analysis.retryAfterSeconds} 秒后重试` : '稍后刷新名额，或先体验训练闭环' }}</span>
          </div>
          <RouterLink to="/mine">快速体验</RouterLink>
        </div>

        <div v-else-if="analysis.status === 'failed' && analysis.failureKind === 'local_gate'" class="result-toast error-toast">
          <div>
            <strong>这个本地视频不符合当前上限</strong>
            <span>{{ analysis.error?.message ?? '请重新选择视频' }}</span>
          </div>
          <div class="local-gate-actions">
            <label>
              重新选择
              <input
                type="file"
                :accept="SUPPORTED_LOCAL_MEDIA_TYPES.join(',')"
                @change="importLocalFile"
              />
            </label>
            <button type="button" @click="retryCapabilities">刷新上限</button>
            <button
              type="button"
              :disabled="!localGateRechecked || !localSourcePassesCurrentGate"
              @click="startAnalysis"
            >
              重新分析当前视频
            </button>
          </div>
        </div>

        <div v-else-if="analysis.status === 'failed'" class="result-toast error-toast">
          <div>
            <strong>{{ analysis.restoreError ? '这次分析已过期' : '这次没有分析成功' }}</strong>
            <span>{{ analysis.error?.message ?? '请稍后重试' }}</span>
          </div>
          <button type="button" @click="startAnalysis">重试</button>
        </div>

        <div v-else-if="analysis.status === 'cancelled'" class="result-toast">
          <div>
            <strong>已取消分析</strong>
            <span>视频仍保留在本机，可以随时重新开始。</span>
          </div>
          <button
            type="button"
            :disabled="!localSourcePassesCurrentGate"
            @click="startAnalysis"
          >
            重新分析
          </button>
        </div>

        <div v-if="analysis.status === 'completed' && !analysis.candidates.length && !analysis.coverageGaps.length" class="result-toast">
          <div>
            <strong>视频里没有找到明确动作</strong>
            <span>可以重试，或直接在草稿中手工创建</span>
          </div>
          <RouterLink to="/plan">去草稿</RouterLink>
        </div>

        <button
          v-if="!mediaLoadFailed && !analysis.isRunning && analysis.status !== 'cancelled' && analysis.failureKind !== 'local_gate' && !(analysis.status === 'completed' && (analysis.candidates.length || analysis.coverageGaps.length))"
          type="button"
          class="analyze-button"
          :disabled="(access.loaded && !access.canAnalyze) || !localSourcePassesCurrentGate"
          @click="startAnalysis"
        >
          <span class="button-crosshair" aria-hidden="true" />
          <span>
            <small>仅在点击后上传并开始分析</small>
            {{ sourceKind === 'local' && !analysis.capabilities?.local_upload_enabled
              ? '本地分析当前不可用'
              : access.loaded && !access.canAnalyze ? '实时 AI 名额暂不可用' : '分析视频动作' }}
          </span>
          <b>＋</b>
        </button>

        <CandidateReviewPanel
          v-if="analysis.status === 'completed' && (analysis.candidates.length || analysis.coverageGaps.length)"
          :candidates="analysis.candidates"
          :warnings="analysis.warnings"
          :max-segment-end="segmentEndLimit"
          :submitting="addingCandidates"
          :submission-error="addCandidatesError"
          :coverage-gaps="analysis.coverageGaps"
          :retrying-gap="analysis.gapRetrying"
          :gap-retry-error="analysis.gapRetryError"
          @preview="preview"
          @add="addCandidates"
          @retry-gap="retryGap"
          @close="returnToVideo"
        />
      </template>
    </section>

    <footer class="page-caption">
      <span>01</span>
      <p>点击后分析整条视频，再选择想练的动作</p>
      <i />
      <small>AGENT PROPOSES · YOU DECIDE</small>
    </footer>
  </main>
</template>

<style scoped>
.video-page {
  position: relative;
  min-height: 100dvh;
  overflow-x: hidden;
  padding: max(16px, env(safe-area-inset-top)) 14px max(18px, env(safe-area-inset-bottom));
}

.source-entry,
.recovery-status {
  position: relative;
  z-index: 6;
  width: min(100%, 430px);
  margin: 0 auto 10px;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: 16px;
  background: rgb(16 20 23 / 88%);
  backdrop-filter: blur(14px);
}

.source-entry > div:first-child { display: grid; gap: 3px; }
.source-entry h1 { margin: 0; font-size: 21px; }
.source-entry p { margin: 0; color: var(--muted); font-size: 11px; line-height: 1.45; }
.source-entry .source-entry-eyebrow { color: var(--cyan); font: 600 11px/1 var(--font-display); letter-spacing: .14em; }
.source-entry .rights-note { margin-top: 3px; }

.local-file-button {
  display: grid;
  min-height: 46px;
  margin-top: 10px;
  place-items: center;
  border-radius: 12px;
  color: var(--bg);
  background: var(--cyan);
  font-size: 13px;
  font-weight: 800;
}

.local-file-button input { position: absolute; width: 1px; height: 1px; opacity: 0; }
.local-file-button.disabled { color: var(--muted); background: var(--surface-raised); opacity: .7; }
.source-entry .local-import-error { margin-top: 8px; color: var(--coral); }
.source-entry .local-storage-note { margin-top: 7px; }
.source-entry .local-storage-note.session_only { color: var(--coral); }

.capability-retry {
  min-height: 44px;
  margin-top: 6px;
  border: 0;
  color: var(--cyan);
  background: transparent;
  font-size: 11px;
  font-weight: 700;
}

.controlled-fallback { margin-top: 8px; color: var(--muted); font-size: 11px; }
.controlled-fallback summary { min-height: 44px; padding: 13px 0 0; cursor: pointer; }
.controlled-fallback label { display: flex; align-items: center; gap: 10px; }
.controlled-fallback select { min-width: 0; min-height: 44px; flex: 1; padding: 0 8px; border: 1px solid var(--line); border-radius: 9px; color: var(--ink); background: var(--surface-raised); }
.controlled-fallback option { color: #111; }
.source-load-error { display: flex; align-items: center; justify-content: space-between; gap: 8px; color: var(--coral); }
.source-load-error button { min-height: 44px; padding: 0 10px; border: 1px solid var(--line); border-radius: 9px; color: var(--cyan); background: transparent; }

.recovery-status { display: flex; align-items: center; justify-content: space-between; gap: 10px; border-color: rgb(38 235 213 / 25%); }
.recovery-status strong,
.recovery-status span { display: block; font-size: 11px; }
.recovery-status span { margin-top: 3px; color: var(--muted); }
.recovery-status button { min-height: 44px; border: 0; color: var(--cyan); background: transparent; font-weight: 700; }

.ambient-grid {
  position: fixed;
  inset: 0;
  pointer-events: none;
  background:
    linear-gradient(rgb(38 235 213 / 3%) 1px, transparent 1px),
    linear-gradient(90deg, rgb(38 235 213 / 3%) 1px, transparent 1px);
  background-size: 42px 42px;
  mask-image: radial-gradient(circle at 50% 30%, black, transparent 72%);
}

.topbar,
.brand-lockup,
.top-actions,
.draft-link,
.source-rail,
.time-readout,
.page-caption {
  display: flex;
  align-items: center;
}

.topbar {
  position: relative;
  z-index: 5;
  justify-content: space-between;
  width: min(100%, 1180px);
  margin: 0 auto 14px;
}

.brand-lockup { gap: 10px; }
.top-actions { gap: 7px; }
.mine-link { display: inline-grid; min-height: 44px; padding: 0 10px; place-items: center; color: var(--muted); font-size: 11px; font-weight: 700; text-decoration: none; }

.brand-mark {
  display: grid;
  width: 34px;
  aspect-ratio: 1;
  place-items: center;
  color: var(--bg);
  background: var(--cyan);
  clip-path: polygon(0 0, 82% 0, 100% 18%, 100% 100%, 18% 100%, 0 82%);
  font: 700 22px/1 var(--font-display);
}

.brand-lockup strong,
.brand-lockup small { display: block; }
.brand-lockup strong { font-size: 13px; letter-spacing: .02em; }
.brand-lockup small { margin-top: 2px; color: var(--muted); font: 500 11px/1 var(--font-display); letter-spacing: .12em; }

.draft-link {
  min-height: 44px;
  gap: 8px;
  padding: 8px 10px 8px 12px;
  border: 1px solid var(--line);
  border-radius: 999px;
  color: var(--ink);
  background: rgb(255 255 255 / 3%);
  font-size: 12px;
  text-decoration: none;
}

.draft-link b {
  display: grid;
  min-width: 22px;
  height: 22px;
  place-items: center;
  border-radius: 50%;
  color: var(--bg);
  background: var(--coral);
  font: 700 14px/1 var(--font-display);
}

.video-stage {
  position: relative;
  width: min(100%, 430px);
  aspect-ratio: 9 / 16;
  margin: 0 auto;
  overflow: hidden;
  border: 1px solid var(--line-strong);
  border-radius: 24px;
  background: #030405;
  box-shadow: 0 30px 90px rgb(0 0 0 / 45%), 0 0 0 1px rgb(255 255 255 / 2%) inset;
}

.source-rail {
  position: absolute;
  z-index: 7;
  top: 14px;
  left: 14px;
  right: 14px;
  gap: 7px;
  min-height: 46px;
  padding: 0 10px;
  border: 1px solid rgb(255 255 255 / 12%);
  border-radius: 10px;
  background: rgb(4 6 7 / 68%);
  backdrop-filter: blur(12px);
}

.live-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--coral);
  box-shadow: 0 0 0 4px rgb(255 111 97 / 15%);
}

.source-rail label { color: var(--muted); font-size: 11px; }
.source-rail > span:not(.live-dot) { color: var(--muted); font-size: 11px; }
.source-rail strong { min-width: 0; overflow: hidden; flex: 1; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.live-dot.local { background: var(--cyan); box-shadow: 0 0 0 4px rgb(38 235 213 / 15%); }
.source-rail select {
  min-width: 0;
  min-height: 44px;
  flex: 1;
  border: 0;
  color: var(--ink);
  background: transparent;
  font-size: 11px;
  outline: 2px solid transparent;
  outline-offset: 3px;
}
.source-rail select:focus-visible { outline-color: var(--cyan); }
.source-rail option { color: #111; }

.source-video {
  width: 100%;
  height: 100%;
  object-fit: cover;
  background: #020303;
}

.video-vignette {
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: linear-gradient(to bottom, rgb(0 0 0 / 32%), transparent 26%, transparent 64%, rgb(0 0 0 / 70%));
}

.time-readout {
  position: absolute;
  z-index: 5;
  left: 16px;
  bottom: 104px;
  gap: 7px;
  font-family: var(--font-display);
}

.time-readout span { color: var(--cyan); font-size: 22px; font-weight: 700; }
.time-readout small { color: var(--muted); font-size: 14px; }
.time-readout i { width: 18px; height: 1px; background: var(--line-strong); }

.analyze-button {
  position: absolute;
  z-index: 6;
  left: 16px;
  right: 16px;
  bottom: 20px;
  display: grid;
  grid-template-columns: 34px 1fr 34px;
  align-items: center;
  gap: 10px;
  padding: 13px 14px;
  border: 1px solid rgb(38 235 213 / 55%);
  border-radius: 14px;
  color: #03110f;
  background: linear-gradient(100deg, var(--cyan), #77f8e8);
  box-shadow: 0 18px 40px rgb(38 235 213 / 24%);
  text-align: left;
}

.stage-media-error {
  position: absolute;
  z-index: 8;
  inset: 76px 14px 20px;
  display: grid;
  place-content: center;
  gap: 8px;
  padding: 24px;
  border: 1px solid rgb(255 111 97 / 35%);
  border-radius: 16px;
  background: rgb(3 5 6 / 92%);
  text-align: center;
}
.stage-media-error strong { color: var(--ink); font-size: 18px; }
.stage-media-error small { color: var(--muted); font-size: 11px; }
.stage-media-error div { display: flex; justify-content: center; gap: 8px; margin-top: 8px; }
.stage-media-error a { display: inline-grid; min-width: 44px; min-height: 44px; padding: 0 12px; place-items: center; border: 1px solid var(--line-strong); border-radius: 10px; color: var(--cyan); font-size: 11px; font-weight: 700; text-decoration: none; }
.analyze-button:disabled { cursor: not-allowed; filter: saturate(.35); opacity: .72; }

.analyze-button span span,
.analyze-button small { display: block; }
.analyze-button small { margin-bottom: 2px; color: rgb(3 17 15 / 60%); font: 600 11px/1 var(--font-display); letter-spacing: .12em; }
.analyze-button span { font-weight: 900; }
.analyze-button b { justify-self: end; font-size: 26px; font-weight: 300; }

.button-crosshair {
  width: 28px;
  height: 28px;
  border: 1px solid rgb(3 17 15 / 50%);
  border-radius: 50%;
  background: linear-gradient(90deg, transparent 48%, rgb(3 17 15 / 50%) 49% 51%, transparent 52%), linear-gradient(transparent 48%, rgb(3 17 15 / 50%) 49% 51%, transparent 52%);
}

.analysis-overlay {
  position: absolute;
  z-index: 12;
  inset: 0;
  display: grid;
  place-items: center;
  background: rgb(3 5 6 / 72%);
  backdrop-filter: blur(5px);
}

.scanner {
  position: absolute;
  left: 8%;
  right: 8%;
  height: 1px;
  background: var(--cyan);
  box-shadow: 0 0 18px var(--cyan);
  animation: scan 2.1s ease-in-out infinite;
}

.analysis-status { position: relative; text-align: center; }
.analysis-status p { margin: 20px 0 6px; font-weight: 800; }
.analysis-status small { color: var(--muted); }
.analysis-status .progress-copy { display: block; margin: 10px 0 4px; color: var(--ink); font: 700 18px/1 var(--font-display); }
.pulse-ring {
  display: block;
  width: 48px;
  height: 48px;
  margin: auto;
  border: 1px solid var(--cyan);
  border-radius: 50%;
  box-shadow: 0 0 0 10px rgb(38 235 213 / 6%), 0 0 30px rgb(38 235 213 / 20%);
  animation: pulse 1.4s ease-in-out infinite;
}

.cancel-button {
  position: absolute;
  bottom: 28px;
  min-width: 44px;
  min-height: 44px;
  padding: 9px 16px;
  border: 1px solid var(--line-strong);
  border-radius: 999px;
  color: var(--muted);
  background: rgb(0 0 0 / 25%);
}

.result-toast {
  position: absolute;
  z-index: 8;
  left: 14px;
  right: 14px;
  bottom: 92px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px;
  border: 1px solid var(--line-strong);
  border-radius: 12px;
  background: rgb(7 9 11 / 90%);
}
.result-toast strong,
.result-toast span { display: block; }
.result-toast strong { font-size: 13px; }
.result-toast span { margin-top: 3px; color: var(--muted); font-size: 11px; }
.result-toast button,
.result-toast a { display: inline-grid; min-width: 44px; min-height: 44px; place-items: center; color: var(--cyan); background: transparent; border: 0; font-weight: 700; text-decoration: none; }
.error-toast { border-color: rgb(255 111 97 / 35%); }
.error-toast button { color: var(--coral); }
.local-gate-actions { display: grid; gap: 2px; }
.local-gate-actions label { position: relative; display: inline-grid; min-width: 44px; min-height: 44px; place-items: center; overflow: hidden; color: var(--coral); font-size: 11px; font-weight: 700; cursor: pointer; }
.local-gate-actions input { position: absolute; width: 1px; height: 1px; opacity: 0; }
.busy-toast { border-color: rgb(255 111 97 / 35%); }
.busy-toast a { color: var(--coral); }

.stage-empty {
  display: grid;
  height: 100%;
  padding: 36px;
  place-content: center;
  text-align: center;
}
.stage-empty h1 { margin: 8px 0; font-size: 26px; }
.stage-empty p { max-width: 260px; margin: 0; color: var(--muted); font-size: 12px; line-height: 1.7; }
.source-load-error button { min-width: 44px; min-height: 44px; justify-self: center; margin-top: 16px; padding: 0 16px; border: 1px solid var(--line-strong); border-radius: 10px; color: var(--ink); background: var(--surface-raised); font-weight: 700; }
.empty-code { color: var(--coral); font: 600 11px/1 var(--font-display); letter-spacing: .16em; }

.page-caption {
  position: relative;
  width: min(100%, 430px);
  gap: 9px;
  margin: 12px auto 0;
  color: var(--muted);
}
.page-caption span { color: var(--coral); font: 700 18px/1 var(--font-display); }
.page-caption p { margin: 0; font-size: 11px; }
.page-caption i { height: 1px; flex: 1; background: var(--line); }
.page-caption small { font: 500 11px/1 var(--font-display); letter-spacing: .1em; }

@keyframes scan { 0%, 100% { top: 20%; opacity: .4; } 50% { top: 78%; opacity: 1; } }
@keyframes pulse { 50% { transform: scale(.86); opacity: .65; } }

@media (min-width: 900px) {
  .video-page { padding-inline: 28px; }
  .video-stage {
    width: min(100%, 430px);
  }
  .video-stage.has-review {
    width: min(100%, 1180px);
    height: min(78dvh, 760px);
    min-height: 620px;
    aspect-ratio: auto;
  }
  .source-video { width: min(100%, 430px); margin-left: calc((100% - 430px) / 2); border-inline: 1px solid var(--line); }
  .video-vignette { left: calc((100% - 430px) / 2); right: calc((100% - 430px) / 2); }
  .time-readout,
  .analyze-button { left: calc((100% - 430px) / 2 + 16px); right: calc((100% - 430px) / 2 + 16px); }
  .video-stage.has-review .source-video { margin-left: 28px; }
  .video-stage.has-review .video-vignette { left: 28px; right: calc(100% - 458px); }
  .video-stage.has-review .time-readout,
  .video-stage.has-review .analyze-button { left: 44px; right: calc(100% - 442px); }
  .source-rail { width: 390px; left: 50%; right: auto; transform: translateX(-50%); }
  .video-stage.has-review .source-rail { left: 43px; transform: none; }
  .page-caption { width: min(100%, 1180px); }
}

@media (prefers-reduced-motion: reduce) {
  .scanner,
  .pulse-ring { animation: none; }
}
</style>
