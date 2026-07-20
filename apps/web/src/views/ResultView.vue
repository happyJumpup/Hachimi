<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import HachimiPet from '@/features/experience/HachimiPet.vue'
import {
  deliverCompletionPoster,
  downloadCompletionPoster,
  renderCompletionPoster,
} from '@/features/experience/poster'
import { useLibraryStore } from '@/stores/library'

const route = useRoute()
const library = useLibraryStore()
const pending = ref(false)
const feedback = ref('')
const recordId = computed(() => String(route.params.recordId ?? ''))
const record = computed(() => library.records.find((entry) => entry.id === recordId.value) ?? null)
const canCreatePoster = computed(() => record.value?.outcome === 'completed')

const formatDuration = (seconds: number): string => {
  const rounded = Math.max(0, Math.round(seconds))
  const minutes = Math.floor(rounded / 60)
  const remainder = rounded % 60
  return `${minutes}:${String(remainder).padStart(2, '0')}`
}

const posterBlob = async (): Promise<Blob> => {
  const current = record.value
  if (!current) throw new Error('训练记录不存在')
  return renderCompletionPoster({
    outcome: current.outcome,
    planName: current.plan.name,
    trainingDurationSeconds: current.trainingDurationSeconds,
    caloriesKcal: current.calorie.value,
    completedActionCount: current.completedActionCount,
  })
}

const sharePoster = async (): Promise<void> => {
  if (pending.value || !record.value) return
  pending.value = true
  feedback.value = ''
  try {
    const result = await deliverCompletionPoster(await posterBlob(), record.value.plan.name)
    feedback.value = result === 'shared' ? '已打开分享' : result === 'downloaded' ? '海报已下载' : '已取消分享'
  } catch (error) {
    feedback.value = error instanceof Error ? error.message : '海报生成失败，请重试'
  } finally {
    pending.value = false
  }
}

const downloadPoster = async (): Promise<void> => {
  if (pending.value || !record.value) return
  pending.value = true
  feedback.value = ''
  try {
    downloadCompletionPoster(await posterBlob(), record.value.plan.name)
    feedback.value = '海报已下载'
  } catch (error) {
    feedback.value = error instanceof Error ? error.message : '海报生成失败，请重试'
  } finally {
    pending.value = false
  }
}

onMounted(() => library.refreshHistory())
</script>

<template>
  <main class="result-page">
    <header><RouterLink to="/mine">← 我的训练</RouterLink><span>训练记录</span></header>

    <section v-if="record" class="result-card" :class="{ completed: record.outcome === 'completed' }">
      <p class="eyebrow">{{ record.outcome === 'completed' ? 'SESSION COMPLETE' : 'SESSION ENDED' }}</p>
      <h1>{{ record.outcome === 'completed' ? '训练完成' : '本次训练已结束' }}</h1>
      <h2>{{ record.plan.name }}</h2>

      <HachimiPet v-if="record.outcome === 'completed'" state="completed" :visible="true" />

      <div class="result-metrics">
        <span><b>{{ formatDuration(record.trainingDurationSeconds) }}</b>训练时长</span>
        <span><b>约 {{ record.calorie.value }}</b>千卡</span>
        <span><b>{{ record.completedActionCount }}</b>完成动作</span>
      </div>

      <div class="action-results">
        <div v-for="action in record.actions" :key="action.itemId">
          <span><strong>{{ action.name }}</strong><small>{{ action.completedSets }} / {{ action.targetSets }} 组</small></span>
          <b>{{ action.status === 'completed' ? '完成' : action.status === 'partial' ? '部分完成' : '已跳过' }}</b>
        </div>
      </div>

      <div v-if="canCreatePoster" class="poster-actions">
        <button type="button" :disabled="pending" @click="sharePoster">分享海报</button>
        <button type="button" class="secondary" :disabled="pending" @click="downloadPoster">下载海报</button>
      </div>
      <p v-else class="early-note">提前结束只保留实际记录，不生成完成海报。</p>
      <p v-if="feedback" class="feedback" role="status">{{ feedback }}</p>

      <footer><RouterLink to="/plan">再练一次</RouterLink><RouterLink to="/">继续找动作</RouterLink></footer>
    </section>

    <section v-else class="missing-result">
      <h1>没有找到这条训练记录</h1>
      <RouterLink to="/mine">返回我的训练</RouterLink>
    </section>
  </main>
</template>

<style scoped>
.result-page { width: min(100%, 430px); min-height: 100dvh; margin: auto; padding: max(18px, env(safe-area-inset-top)) 16px max(30px, env(safe-area-inset-bottom)); }
.result-page > header,
.result-metrics,
.action-results div,
.poster-actions,
.result-card footer { display: flex; align-items: center; }
.result-page > header { justify-content: space-between; }
.result-page > header a { color: var(--ink); font-size: 11px; font-weight: 700; text-decoration: none; }
.result-page > header span { color: var(--muted); font-size: 9px; }
.result-card { margin-top: 24px; padding: 22px; border: 1px solid var(--line-strong); border-radius: 24px; background: linear-gradient(160deg, var(--surface-raised), var(--surface)); }
.result-card.completed { border-color: rgb(38 235 213 / 35%); }
.eyebrow { margin: 0; color: var(--cyan); font: 600 10px/1 var(--font-display); letter-spacing: .14em; }
.result-card h1 { margin: 10px 0 4px; font: 700 48px/.9 var(--font-display), var(--font-cn); }
.result-card h2 { margin: 0; color: var(--muted); font-size: 14px; font-weight: 600; }
.result-card :deep(.hachimi-pet) { margin: 18px auto -4px; }
.result-metrics { justify-content: space-between; gap: 8px; margin: 20px 0; padding: 16px 0; border-block: 1px solid var(--line); }
.result-metrics span { color: var(--muted); font-size: 9px; text-align: center; }
.result-metrics b { display: block; margin-bottom: 4px; color: var(--ink); font: 700 25px/1 var(--font-display); }
.action-results { display: grid; }
.action-results div { justify-content: space-between; gap: 10px; padding: 11px 0; border-bottom: 1px solid var(--line); }
.action-results strong,
.action-results small { display: block; }
.action-results small { margin-top: 3px; color: var(--muted); font-size: 9px; }
.action-results > div > b { color: var(--cyan); font-size: 9px; }
.poster-actions { gap: 8px; margin-top: 18px; }
.poster-actions button { min-height: 48px; flex: 1; border: 0; border-radius: 12px; color: var(--bg); background: var(--cyan); font-weight: 800; }
.poster-actions .secondary { color: var(--ink); border: 1px solid var(--line); background: transparent; }
.early-note,
.feedback { color: var(--muted); font-size: 10px; text-align: center; }
.feedback { color: var(--cyan); }
.result-card footer { justify-content: center; gap: 18px; margin-top: 18px; }
.result-card footer a { color: var(--muted); font-size: 10px; text-decoration: none; }
.missing-result { margin-top: 25vh; text-align: center; }
.missing-result h1 { font-size: 24px; }
.missing-result a { color: var(--cyan); }
</style>
