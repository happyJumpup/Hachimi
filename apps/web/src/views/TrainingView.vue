<script setup lang="ts">
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from 'vue'
import { onBeforeRouteLeave } from 'vue-router'

import { analysisClient } from '@/api/client'
import HachimiPet from '@/features/experience/HachimiPet.vue'
import { derivePetState } from '@/features/experience/pet-state'
import { useAnalysisStore } from '@/stores/analysis'
import { useLibraryStore } from '@/stores/library'
import { useTrainingStore } from '@/stores/training'

const analysis = useAnalysisStore()
const library = useLibraryStore()
const training = useTrainingStore()
const video = ref<HTMLVideoElement | null>(null)
const nowMilliseconds = ref(Date.now())
const commandPending = ref(false)
let ticker: ReturnType<typeof setInterval> | null = null
let pausePending = false

const session = computed(() => training.session)
const item = computed(() => training.currentItem)
const progress = computed(() => training.currentProgress)
const source = computed(() => {
  const sourceId = item.value?.sourceRef?.sourceId
  return sourceId ? analysis.sources.find((entry) => entry.id === sourceId) ?? null : null
})
const segment = computed(() => item.value?.segment.value ?? null)
const targetSets = computed(() => item.value?.sets.value ?? 0)
const actionPosition = computed(() => {
  if (!session.value) return ''
  return `动作 ${session.value.currentItemIndex + 1} / ${session.value.plan.items.length}`
})
const setNumber = computed(() => Math.min(
  (session.value?.currentSetIndex ?? 0) + 1,
  targetSets.value,
))
const restRemainingSeconds = computed(() => {
  if (session.value?.status !== 'resting' || !session.value.restEndsAt) return 0
  return Math.max(0, Math.ceil(
    (Date.parse(session.value.restEndsAt) - nowMilliseconds.value) / 1_000,
  ))
})
const durationRemainingSeconds = computed(() => {
  if (item.value?.mode !== 'duration') return 0
  const target = item.value.durationSeconds.value ?? 0
  const elapsed = (session.value?.currentSetActiveMilliseconds ?? 0) / 1_000
  return Math.max(0, Math.ceil(target - elapsed))
})
const statusLabel = computed(() => {
  if (!session.value) return '没有未完成训练'
  if (session.value.status === 'active') return item.value?.mode === 'duration' ? '倒计时进行中' : '本组进行中'
  if (session.value.status === 'resting') return '组间休息'
  if (session.value.status === 'ready_to_continue') return '准备继续'
  if (session.value.pauseReason === 'before_start') return '准备开始'
  if (session.value.pauseReason === 'between_actions') return '下一个动作'
  if (session.value.pauseReason === 'recovered') return '已恢复并暂停'
  return '训练已暂停'
})
const petState = computed(() => derivePetState({
  sessionStatus: session.value?.status ?? null,
  pauseReason: session.value?.pauseReason ?? null,
  outcome: training.lastRecord?.outcome ?? null,
}))

const formatDuration = (seconds: number): string => {
  const safe = Math.max(0, Math.round(seconds))
  const minutes = Math.floor(safe / 60)
  return `${minutes}:${String(safe % 60).padStart(2, '0')}`
}

const syncVideo = async (): Promise<void> => {
  await nextTick()
  const element = video.value
  const range = segment.value
  if (!element || !range) return
  if (element.currentTime < range.start_seconds || element.currentTime >= range.end_seconds) {
    element.currentTime = range.start_seconds
  }
  if (session.value?.status === 'active' && !training.commandLocked) {
    await element.play().catch(() => undefined)
  } else {
    element.pause()
  }
}

const keepVideoInSegment = (): void => {
  const element = video.value
  const range = segment.value
  if (!element || !range) return
  if (element.currentTime >= range.end_seconds || element.currentTime < range.start_seconds) {
    element.currentTime = range.start_seconds
    if (session.value?.status === 'active') void element.play().catch(() => undefined)
  }
}

watch(
  () => [session.value?.status, item.value?.id, training.commandLocked],
  () => { void syncVideo() },
)

const run = async (operation: () => Promise<unknown>): Promise<void> => {
  if (commandPending.value) return
  commandPending.value = true
  await operation()
  commandPending.value = false
  await syncVideo()
}

const startOrContinue = (): Promise<void> => run(async () => {
  if (session.value?.status === 'ready_to_continue' || session.value?.status === 'resting') {
    await training.continueRest()
  } else {
    await training.startSet()
  }
})

const pause = (): Promise<void> => run(() => training.pause('user'))
const completeSet = (): Promise<void> => run(() => training.completeSet())
const continueEarly = (): Promise<void> => run(() => training.continueRest())
const reloadAfterConflict = (): Promise<void> => run(() => training.restore())

const endEarly = async (): Promise<void> => {
  if (!window.confirm('提前结束后只记录实际完成量，确定结束吗？')) return
  await run(() => training.endEarly())
}

const skipRemaining = async (): Promise<void> => {
  if (commandPending.value) return
  commandPending.value = true
  const result = await training.skipAction()
  commandPending.value = false
  if (!result.ok && result.code === 'end_confirmation_required') {
    await endEarly()
    return
  }
  await syncVideo()
}

const pauseForLeave = async (): Promise<void> => {
  if (pausePending || training.session?.status !== 'active') return
  pausePending = true
  await training.pause('page_hidden')
  pausePending = false
}

const handleVisibility = (): void => {
  if (document.hidden) void pauseForLeave()
}

const handlePageHide = (): void => {
  void pauseForLeave()
}

onBeforeRouteLeave(async () => {
  await pauseForLeave()
  return true
})

onMounted(async () => {
  await training.restore()
  if (!analysis.sources.length) await analysis.loadSources(analysisClient)
  document.addEventListener('visibilitychange', handleVisibility)
  window.addEventListener('pagehide', handlePageHide)
  ticker = setInterval(() => {
    nowMilliseconds.value = Date.now()
    const current = training.session
    const expiredRest = current?.status === 'resting'
      && current.restEndsAt !== null
      && Date.now() >= Date.parse(current.restEndsAt)
    if (
      !commandPending.value
      && !training.commandLocked
      && (current?.status === 'active' || expiredRest)
    ) {
      void run(() => training.tick())
    }
  }, 1_000)
  await syncVideo()
})

onBeforeUnmount(() => {
  if (ticker) clearInterval(ticker)
  document.removeEventListener('visibilitychange', handleVisibility)
  window.removeEventListener('pagehide', handlePageHide)
})
</script>

<template>
  <main class="training-page">
    <header class="training-header">
      <RouterLink to="/plan">← 返回方案</RouterLink>
      <div>
        <button type="button" @click="library.setPetVisible(!library.preferences.petVisible)">
          {{ library.preferences.petVisible ? '隐藏哈肌咪' : '显示哈肌咪' }}
        </button>
        <span>{{ statusLabel }}</span>
      </div>
    </header>

    <section v-if="!session && training.lastRecord" class="terminal-card">
      <p class="eyebrow">SESSION SAVED</p>
      <h1>{{ training.lastRecord.outcome === 'completed' ? '训练完成' : '已提前结束' }}</h1>
      <HachimiPet
        v-if="training.lastRecord.outcome === 'completed'"
        state="completed"
        :visible="library.preferences.petVisible"
      />
      <div class="terminal-metrics">
        <span><b>{{ training.lastRecord.completedActionCount }}</b> 完成动作</span>
        <span><b>{{ formatDuration(training.lastRecord.trainingDurationSeconds) }}</b> 训练时长</span>
        <span><b>约 {{ training.lastRecord.calorie.value }}</b> 千卡</span>
      </div>
      <p>实际完成量已保存到本机。</p>
      <RouterLink class="primary-link" :to="`/result/${training.lastRecord.id}`">查看训练结果</RouterLink>
    </section>

    <section v-else-if="!session" class="training-empty">
      <p class="eyebrow">NO ACTIVE SESSION</p>
      <h1>还没有未完成训练</h1>
      <p>先从方案草稿开始一场训练。</p>
      <RouterLink class="primary-link" to="/plan">返回方案</RouterLink>
    </section>

    <template v-else-if="item">
      <section class="session-title">
        <div>
          <p class="action-position">{{ actionPosition }}</p>
          <p class="eyebrow">{{ session.plan.name }}</p>
          <h1>{{ item.name }}</h1>
        </div>
        <div class="set-counter">
          <b>{{ setNumber }}</b>
          <span>/ {{ targetSets }} 组</span>
        </div>
      </section>

      <section class="media-stage" :class="{ 'no-video': !item.sourceRef }">
        <video
          v-if="item.sourceRef && source && segment"
          ref="video"
          :src="source.media_url"
          playsinline
          controls
          preload="metadata"
          @loadedmetadata="syncVideo"
          @timeupdate="keepVideoInSegment"
        />
        <div v-else-if="item.sourceRef" class="media-placeholder">
          <span>正在读取参考视频…</span>
        </div>
        <div v-else class="media-placeholder">
          <span class="no-video-mark">NO VIDEO</span>
          <strong>这个动作没有参考视频</strong>
          <small>按自己的节奏完成本组即可</small>
        </div>
        <HachimiPet
          class="training-pet"
          :state="petState"
          :visible="library.preferences.petVisible"
        />
        <div class="stage-badge">{{ item.sourceRef ? '演示片段循环' : '自建动作' }}</div>
      </section>

      <section class="training-console">
        <div class="status-readout">
          <span>{{ statusLabel }}</span>
          <strong v-if="session.status === 'resting'">{{ formatDuration(restRemainingSeconds) }}</strong>
          <strong v-else-if="item.mode === 'duration'">{{ formatDuration(durationRemainingSeconds) }}</strong>
          <strong v-else>{{ item.reps.value }} 次</strong>
          <small>
            已完成 {{ progress?.completedSets ?? 0 }} / {{ targetSets }} 组
          </small>
        </div>

        <button
          v-if="session.status === 'paused' || session.status === 'ready_to_continue'"
          type="button"
          class="primary-action"
          :disabled="commandPending || training.commandLocked"
          @click="startOrContinue"
        >
          {{ session.status === 'ready_to_continue' ? '准备继续' : '开始本组' }}
        </button>
        <button
          v-else-if="session.status === 'resting'"
          type="button"
          class="primary-action rest-action"
          :disabled="commandPending || training.commandLocked"
          @click="continueEarly"
        >
          提前继续
        </button>
        <button
          v-else-if="item.mode === 'reps'"
          type="button"
          class="primary-action"
          :disabled="commandPending || training.commandLocked"
          @click="completeSet"
        >
          完成本组
        </button>
        <button
          v-else
          type="button"
          class="primary-action pause-action"
          :disabled="commandPending || training.commandLocked"
          @click="pause"
        >
          暂停倒计时
        </button>

        <div class="secondary-actions">
          <button
            v-if="session.status === 'active' && item.mode === 'reps'"
            type="button"
            :disabled="commandPending || training.commandLocked"
            @click="pause"
          >
            暂停
          </button>
          <button
            v-if="session.status === 'active'"
            type="button"
            :disabled="commandPending || training.commandLocked"
            @click="skipRemaining"
          >
            跳过剩余组
          </button>
          <button type="button" class="danger" :disabled="commandPending || training.commandLocked" @click="endEarly">
            提前结束
          </button>
        </div>
      </section>

      <p v-if="training.errorMessage" class="training-error" role="alert">
        {{ training.errorMessage }}
        <button
          v-if="training.commandLocked"
          type="button"
          :disabled="commandPending"
          @click="reloadAfterConflict"
        >
          重新加载最新进度
        </button>
      </p>
    </template>
  </main>
</template>

<style scoped>
.training-page {
  width: min(100%, 430px);
  min-height: 100dvh;
  margin: auto;
  padding: max(18px, env(safe-area-inset-top)) 16px max(24px, env(safe-area-inset-bottom));
}
.training-header,
.session-title,
.terminal-metrics,
.secondary-actions { display: flex; align-items: center; }
.training-header { justify-content: space-between; gap: 12px; margin-bottom: 22px; }
.training-header a { color: var(--ink); font-size: 11px; font-weight: 700; text-decoration: none; }
.training-header > div { display: flex; align-items: center; gap: 8px; }
.training-header button { min-height: 44px; padding: 0 8px; border: 0; color: var(--muted); background: transparent; font-size: 9px; }
.training-header span { color: var(--cyan); font-size: 10px; letter-spacing: .08em; }
.eyebrow { margin: 0; color: var(--cyan); font: 600 10px/1 var(--font-display); letter-spacing: .14em; text-transform: uppercase; }
.action-position { margin: 0 0 7px; color: var(--ink); font-size: 11px; font-weight: 800; }
.session-title { justify-content: space-between; gap: 16px; margin-bottom: 16px; }
.session-title h1 { margin: 6px 0 0; font: 700 38px/.95 var(--font-display), var(--font-cn); }
.set-counter { flex: 0 0 auto; color: var(--muted); text-align: right; }
.set-counter b { color: var(--ink); font: 700 42px/.8 var(--font-display); }
.set-counter span { font-size: 11px; }
.media-stage { position: relative; overflow: hidden; aspect-ratio: 9 / 12; border: 1px solid var(--line); border-radius: 22px; background: #030405; }
.media-stage video { width: 100%; height: 100%; object-fit: cover; }
.media-placeholder { display: grid; height: 100%; place-content: center; gap: 8px; padding: 20px; color: var(--muted); text-align: center; }
.media-placeholder strong { color: var(--ink); font-size: 18px; }
.media-placeholder small { font-size: 11px; }
.no-video-mark { color: var(--coral); font: 700 12px/1 var(--font-display); letter-spacing: .16em; }
.stage-badge { position: absolute; top: 12px; left: 12px; padding: 7px 9px; border: 1px solid var(--line); border-radius: 999px; color: var(--ink); background: rgb(7 9 11 / 78%); font-size: 9px; backdrop-filter: blur(10px); }
.training-pet { position: absolute; right: 12px; bottom: 38px; z-index: 3; }
.training-console { position: relative; z-index: 2; margin-top: -28px; padding: 18px; border: 1px solid var(--line-strong); border-radius: 20px; background: rgb(16 20 23 / 96%); box-shadow: 0 20px 50px rgb(0 0 0 / 48%); }
.status-readout { display: grid; justify-items: center; margin-bottom: 14px; }
.status-readout span { color: var(--cyan); font-size: 10px; font-weight: 700; }
.status-readout strong { margin: 5px 0; font: 700 54px/.9 var(--font-display); }
.status-readout small { color: var(--muted); font-size: 10px; }
.primary-action,
.primary-link { display: grid; width: 100%; min-height: 50px; place-items: center; border: 0; border-radius: 14px; color: var(--bg); background: var(--cyan); font-weight: 800; text-decoration: none; }
.rest-action { background: var(--coral); }
.pause-action { color: var(--ink); background: var(--surface-raised); }
.primary-action:disabled { opacity: .5; }
.secondary-actions { justify-content: center; gap: 5px; margin-top: 10px; }
.secondary-actions button { min-height: 44px; padding: 0 8px; border: 0; color: var(--muted); background: transparent; font-size: 10px; }
.secondary-actions .danger { color: var(--coral); }
.training-error { margin: 12px 0 0; padding: 10px; border-radius: 10px; color: var(--coral); background: rgb(255 111 97 / 8%); font-size: 11px; text-align: center; }
.training-error button { display: block; min-height: 44px; margin: 6px auto 0; padding: 0 12px; border: 1px solid rgb(255 111 97 / 35%); border-radius: 9px; color: var(--coral); background: transparent; font-weight: 700; }
.terminal-card,
.training-empty { margin-top: 12vh; padding: 28px; border: 1px solid var(--line-strong); border-radius: 22px; background: var(--surface); text-align: center; }
.terminal-card h1,
.training-empty h1 { margin: 10px 0; font: 700 44px/1 var(--font-display), var(--font-cn); }
.terminal-card > p:not(.eyebrow),
.training-empty > p:not(.eyebrow) { color: var(--muted); font-size: 12px; }
.terminal-metrics { justify-content: center; gap: 26px; margin: 24px 0; }
.terminal-metrics span { color: var(--muted); font-size: 10px; }
.terminal-metrics b { display: block; color: var(--ink); font: 700 28px/1 var(--font-display); }
.terminal-card :deep(.hachimi-pet) { margin: 18px auto -8px; }
</style>
