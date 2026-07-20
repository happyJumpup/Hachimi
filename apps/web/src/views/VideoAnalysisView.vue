<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import AccessStatus from '@/components/AccessStatus.vue'
import CandidateReviewPanel from '@/components/CandidateReviewPanel.vue'
import { analysisClient, browserEventStreamFactory } from '@/api/client'
import type { AnalysisCandidate, Segment } from '@/domain/types'
import { useAccessStore } from '@/stores/access'
import { useAnalysisStore } from '@/stores/analysis'
import { useDraftStore } from '@/stores/draft'

const router = useRouter()
const access = useAccessStore()
const analysis = useAnalysisStore()
const draft = useDraftStore()
const video = ref<HTMLVideoElement>()
const selectedSourceId = ref('')
const currentSeconds = ref(0)
const durationSeconds = ref(0)
const previewEnd = ref<number | null>(null)
let resumeAfterCancel = false

const selectedSource = computed(() =>
  analysis.sources.find((source) => source.id === selectedSourceId.value),
)

const formatTime = (seconds: number): string => {
  const safe = Number.isFinite(seconds) ? Math.max(0, seconds) : 0
  return `${Math.floor(safe / 60)}:${Math.floor(safe % 60).toString().padStart(2, '0')}`
}

const loadSources = async (): Promise<void> => {
  await analysis.loadSources(analysisClient)
  if (!analysis.sources.some((source) => source.id === selectedSourceId.value)) {
    selectedSourceId.value = analysis.sources[0]?.id ?? ''
  }
}

onMounted(loadSources)

watch(selectedSourceId, async (next, previous) => {
  if (previous && next !== previous && analysis.isRunning) {
    await analysis.cancel(analysisClient)
  }
  analysis.clearResult()
  currentSeconds.value = 0
  previewEnd.value = null
  video.value?.load()
})

onBeforeUnmount(() => {
  // Always advance the store generation. If createRun is still in flight,
  // start() will cancel the returned run before it can open an SSE stream.
  void analysis.cancel(analysisClient)
})

const syncTime = (): void => {
  if (!video.value) return
  currentSeconds.value = video.value.currentTime
  durationSeconds.value = video.value.duration || selectedSource.value?.duration_seconds || 0
  if (previewEnd.value !== null && video.value.currentTime >= previewEnd.value) {
    video.value.pause()
    previewEnd.value = null
  }
}

const startAnalysis = async (): Promise<void> => {
  if (!video.value || !selectedSourceId.value || analysis.isRunning) return
  resumeAfterCancel = !video.value.paused
  video.value.pause()
  previewEnd.value = null
  await analysis.start({
    sourceId: selectedSourceId.value,
    triggerSeconds: video.value.currentTime,
    client: analysisClient,
    events: browserEventStreamFactory,
  })
  if (analysis.failureKind === 'capacity') await access.load()
}

const cancelAnalysis = async (): Promise<void> => {
  await analysis.cancel(analysisClient)
  if (resumeAfterCancel) await video.value?.play().catch(() => undefined)
  resumeAfterCancel = false
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
): Promise<void> => {
  const sourceSnapshots = Object.fromEntries(
    analysis.sources.map((source) => [source.id, source]),
  )
  draft.addCandidates(candidates, editedSegmentIds, sourceSnapshots)
  await draft.flushPersist()
  analysis.clearResult()
  await router.push('/plan')
}

const returnToVideo = async (): Promise<void> => {
  previewEnd.value = null
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

    <section class="video-stage" :class="{ 'has-review': analysis.status === 'completed' && analysis.candidates.length }">
      <div class="source-rail">
        <span class="live-dot" />
        <label for="source">来源视频</label>
        <select id="source" v-model="selectedSourceId" :disabled="analysis.isRunning">
          <option v-for="source in analysis.sources" :key="source.id" :value="source.id">
            {{ source.title }}
          </option>
        </select>
      </div>

      <div v-if="analysis.sourcesLoading" class="stage-empty">正在读取受控视频源…</div>
      <div v-else-if="analysis.sourcesError && !analysis.sources.length" class="stage-empty source-load-error" role="alert">
        <span class="empty-code">TRY AGAIN</span>
        <h1>{{ analysis.sourcesError }}</h1>
        <p>请检查网络后重试，已经编排的动作不会受影响。</p>
        <button type="button" :disabled="analysis.sourcesLoading" @click="loadSources">
          {{ analysis.sourcesLoading ? '正在重试…' : '重新读取视频' }}
        </button>
      </div>
      <div v-else-if="!selectedSource" class="stage-empty">
        <span class="empty-code">NO SOURCE</span>
        <h1>还没有可分析的视频</h1>
        <p>在本地环境中配置演示视频后，这里会出现来源视频。</p>
      </div>
      <template v-else>
        <video
          ref="video"
          class="source-video"
          :src="selectedSource.media_url"
          playsinline
          controls
          preload="metadata"
          @loadedmetadata="syncTime"
          @timeupdate="syncTime"
        />

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
            <small>只分析当前时间附近</small>
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

        <div v-else-if="analysis.status === 'failed'" class="result-toast error-toast">
          <div>
            <strong>这次没有分析成功</strong>
            <span>{{ analysis.error?.message ?? '请稍后重试' }}</span>
          </div>
          <button type="button" @click="startAnalysis">重试</button>
        </div>

        <div v-if="analysis.status === 'completed' && !analysis.candidates.length" class="result-toast">
          <div>
            <strong>附近没有找到明确动作</strong>
            <span>换个时间点，或直接在草稿中手工创建</span>
          </div>
          <RouterLink to="/plan">去草稿</RouterLink>
        </div>

        <button
          v-if="!analysis.isRunning && !(analysis.status === 'completed' && analysis.candidates.length)"
          type="button"
          class="analyze-button"
          :disabled="access.loaded && !access.canAnalyze"
          @click="startAnalysis"
        >
          <span class="button-crosshair" aria-hidden="true" />
          <span>
            <small>AT {{ formatTime(currentSeconds) }}</small>
            {{ access.loaded && !access.canAnalyze ? '实时 AI 名额暂不可用' : '添加动作' }}
          </span>
          <b>＋</b>
        </button>

        <CandidateReviewPanel
          v-if="analysis.status === 'completed' && analysis.candidates.length"
          :candidates="analysis.candidates"
          :warnings="analysis.warnings"
          @preview="preview"
          @add="addCandidates"
          @close="returnToVideo"
        />
      </template>
    </section>

    <footer class="page-caption">
      <span>01</span>
      <p>播放到想练的动作附近，再点击“添加动作”</p>
      <i />
      <small>AGENT PROPOSES · YOU DECIDE</small>
    </footer>
  </main>
</template>

<style scoped>
.video-page {
  position: relative;
  min-height: 100dvh;
  overflow: hidden;
  padding: max(16px, env(safe-area-inset-top)) 14px max(18px, env(safe-area-inset-bottom));
}

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
.mine-link { min-height: 40px; padding: 0 10px; color: var(--muted); font-size: 10px; font-weight: 700; text-decoration: none; }

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
.brand-lockup small { margin-top: 2px; color: var(--muted); font: 500 9px/1 var(--font-display); letter-spacing: .12em; }

.draft-link {
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
  height: min(72dvh, 700px);
  min-height: 560px;
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
  padding: 8px 10px;
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

.source-rail label { color: var(--muted); font-size: 10px; }
.source-rail select {
  min-width: 0;
  flex: 1;
  border: 0;
  color: var(--ink);
  background: transparent;
  font-size: 11px;
  outline: none;
}
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
.analyze-button:disabled { cursor: not-allowed; filter: saturate(.35); opacity: .72; }

.analyze-button span span,
.analyze-button small { display: block; }
.analyze-button small { margin-bottom: 2px; color: rgb(3 17 15 / 60%); font: 600 10px/1 var(--font-display); letter-spacing: .12em; }
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
.result-toast span { margin-top: 3px; color: var(--muted); font-size: 10px; }
.result-toast button,
.result-toast a { color: var(--cyan); background: transparent; border: 0; font-weight: 700; text-decoration: none; }
.error-toast { border-color: rgb(255 111 97 / 35%); }
.error-toast button { color: var(--coral); }
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
.page-caption small { font: 500 9px/1 var(--font-display); letter-spacing: .1em; }

@keyframes scan { 0%, 100% { top: 20%; opacity: .4; } 50% { top: 78%; opacity: 1; } }
@keyframes pulse { 50% { transform: scale(.86); opacity: .65; } }

@media (min-width: 900px) {
  .video-page { padding-inline: 28px; }
  .video-stage {
    width: min(100%, 1180px);
    height: min(78dvh, 760px);
    min-height: 620px;
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
