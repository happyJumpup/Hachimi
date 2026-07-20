<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

import type { ActionMode, DraftItem } from '@/domain/types'
import { useDraftStore } from '@/stores/draft'
import { useTrainingStore } from '@/stores/training'

const router = useRouter()
const draft = useDraftStore()
const training = useTrainingStore()
const manualName = ref('')
const manualMode = ref<ActionMode>('reps')
const showManual = ref(false)
const starting = ref(false)

const totalSets = computed(() =>
  draft.items.reduce((sum, item) => sum + (item.sets.value ?? 0), 0),
)

const sourceLabel = (item: DraftItem): string =>
  item.sourceRef
    ? `视频动作 · ${item.sourceRef.title ?? item.sourceRef.sourceId}`
    : '自建动作 · 无参考视频'

const provenance = (source: DraftItem['sets']['source']): string => {
  if (source === 'video') return '视频'
  if (source === 'rule') return '规则'
  if (source === 'user') return '已改'
  return '未填'
}

const addManual = (): void => {
  if (!manualName.value.trim()) return
  draft.addManualAction({ name: manualName.value, mode: manualMode.value })
  manualName.value = ''
  showManual.value = false
}

const startTraining = async (): Promise<void> => {
  if (!draft.items.length || starting.value) return
  starting.value = true
  try {
    await draft.flushPersist()
    const result = await training.createFromDraft(draft.plan)
    if (result.ok || result.code === 'active_session_exists') {
      await router.push('/training')
    }
  } finally {
    starting.value = false
  }
}
</script>

<template>
  <main class="plan-page">
    <header class="plan-header">
      <RouterLink to="/" class="back-link">← 继续找动作</RouterLink>
      <span class="save-state"><i /> 已自动保存到本机</span>
    </header>

    <section class="plan-hero">
      <div>
        <p class="eyebrow">CURRENT DRAFT / 01</p>
        <h1>训练方案草稿</h1>
        <p>来自不同视频的动作，在这里排成一次训练。</p>
      </div>
      <div class="plan-metrics">
        <span><b>{{ draft.items.length }}</b> 动作</span>
        <span><b>{{ totalSets }}</b> 组</span>
      </div>
    </section>

    <section v-if="draft.items.length" class="plan-list" aria-label="动作安排">
      <article v-for="(item, index) in draft.items" :key="item.id" class="plan-card">
        <div class="order-column">
          <span>{{ String(index + 1).padStart(2, '0') }}</span>
          <i />
          <div>
            <button type="button" aria-label="上移" :disabled="index === 0" @click="draft.move(item.id, -1)">↑</button>
            <button type="button" aria-label="下移" :disabled="index === draft.items.length - 1" @click="draft.move(item.id, 1)">↓</button>
          </div>
        </div>

        <div class="card-content">
          <p class="source-label">{{ sourceLabel(item) }}</p>
          <input
            class="plan-name"
            :value="item.name"
            aria-label="动作名称"
            @change="draft.updateName(item.id, ($event.target as HTMLInputElement).value)"
          />

          <div class="mode-toggle">
            <button type="button" :class="{ active: item.mode === 'reps' }" @click="draft.updateMode(item.id, 'reps')">按次数</button>
            <button type="button" :class="{ active: item.mode === 'duration' }" @click="draft.updateMode(item.id, 'duration')">按时长</button>
          </div>

          <div class="parameter-grid">
            <label>
              <span>组数 <em>{{ provenance(item.sets.source) }}</em></span>
              <input
                type="number"
                min="1"
                :value="item.sets.value ?? ''"
                @input="draft.updateValue(item.id, 'sets', Number(($event.target as HTMLInputElement).value) || null)"
              />
            </label>
            <label v-if="item.mode === 'reps'">
              <span>每组次数 <em>{{ provenance(item.reps.source) }}</em></span>
              <input
                type="number"
                min="1"
                :value="item.reps.value ?? ''"
                @input="draft.updateValue(item.id, 'reps', Number(($event.target as HTMLInputElement).value) || null)"
              />
            </label>
            <label v-else>
              <span>每组秒数 <em>{{ provenance(item.durationSeconds.source) }}</em></span>
              <input
                type="number"
                min="1"
                :value="item.durationSeconds.value ?? ''"
                @input="draft.updateValue(item.id, 'durationSeconds', Number(($event.target as HTMLInputElement).value) || null)"
              />
            </label>
            <label>
              <span>休息秒数 <em>{{ provenance(item.restSeconds.source) }}</em></span>
              <input
                type="number"
                min="0"
                :value="item.restSeconds.value ?? ''"
                @input="draft.updateValue(item.id, 'restSeconds', Number(($event.target as HTMLInputElement).value) || 0)"
              />
            </label>
            <label>
              <span>重量 kg <em>{{ provenance(item.weightKg.source) }}</em></span>
              <input
                type="number"
                min="0"
                step="0.5"
                placeholder="留空"
                :value="item.weightKg.value ?? ''"
                @input="draft.updateValue(item.id, 'weightKg', Number(($event.target as HTMLInputElement).value) || null)"
              />
            </label>
          </div>

          <div class="segment-line" v-if="item.segment.value">
            <span>演示片段</span>
            <b>{{ item.segment.value.start_seconds.toFixed(1) }}s—{{ item.segment.value.end_seconds.toFixed(1) }}s</b>
            <em>{{ provenance(item.segment.source) }}</em>
          </div>

          <footer class="card-actions">
            <button type="button" @click="draft.duplicate(item.id)">复制</button>
            <button type="button" class="danger" @click="draft.remove(item.id)">删除</button>
          </footer>
        </div>
      </article>
    </section>

    <section v-else class="empty-plan">
      <span>00</span>
      <h2>草稿还是空的</h2>
      <p>可以继续找动作，也可以直接创建一个没有参考视频的自建动作。</p>
    </section>

    <section class="manual-section">
      <button v-if="!showManual" type="button" class="manual-trigger" @click="showManual = true">
        <span>＋</span>
        <div><strong>创建动作</strong><small>没有参考视频也可以</small></div>
      </button>
      <form v-else class="manual-form" @submit.prevent="addManual">
        <div>
          <p class="eyebrow">MANUAL ACTION</p>
          <h2>创建自建动作</h2>
        </div>
        <label>
          动作名称
          <input v-model="manualName" autofocus placeholder="例如：平板支撑" />
        </label>
        <div class="mode-toggle">
          <button type="button" :class="{ active: manualMode === 'reps' }" @click="manualMode = 'reps'">按次数</button>
          <button type="button" :class="{ active: manualMode === 'duration' }" @click="manualMode = 'duration'">按时长</button>
        </div>
        <div class="manual-actions">
          <button type="button" @click="showManual = false">取消</button>
          <button type="submit" class="confirm" :disabled="!manualName.trim()">加入草稿</button>
        </div>
      </form>
    </section>

    <section v-if="draft.items.length" class="start-training-panel">
      <div>
        <strong>{{ training.hasCurrent ? '已有未完成训练' : '方案结构会在开始前检查' }}</strong>
        <small>{{ training.hasCurrent ? '继续当前进度，不会覆盖原场次' : '不评价动作顺序或训练效果' }}</small>
      </div>
      <button
        v-if="!training.hasCurrent"
        type="button"
        :disabled="starting"
        @click="startTraining"
      >
        {{ starting ? '正在准备…' : '开始训练' }}
      </button>
      <RouterLink v-else to="/training">继续训练</RouterLink>
    </section>

    <section v-if="training.errorCode === 'invalid_plan'" class="plan-error" role="alert">
      <strong>方案还不能开始训练</strong>
      <p v-for="issue in training.validationIssues" :key="`${issue.itemId}-${issue.field}`">
        {{ issue.message }}
      </p>
    </section>
  </main>
</template>

<style scoped>
.plan-page {
  width: min(100%, 880px);
  min-height: 100dvh;
  margin: auto;
  padding: max(18px, env(safe-area-inset-top)) 16px max(36px, env(safe-area-inset-bottom));
}

.plan-header,
.plan-hero,
.plan-metrics,
.save-state,
.segment-line,
.card-actions,
.manual-trigger,
.manual-actions {
  display: flex;
  align-items: center;
}

.plan-header { justify-content: space-between; margin-bottom: 34px; }
.back-link { color: var(--ink); font-size: 12px; font-weight: 700; text-decoration: none; }
.save-state { gap: 6px; color: var(--muted); font-size: 10px; }
.save-state i { width: 6px; height: 6px; border-radius: 50%; background: var(--cyan); box-shadow: 0 0 12px var(--cyan); }

.plan-hero { align-items: end; justify-content: space-between; gap: 20px; padding-bottom: 22px; border-bottom: 1px solid var(--line); }
.eyebrow { margin: 0; color: var(--cyan); font: 600 11px/1 var(--font-display); letter-spacing: .15em; }
.plan-hero h1 { margin: 8px 0 4px; font: 700 clamp(38px, 10vw, 64px)/.95 var(--font-display), var(--font-cn); letter-spacing: -.025em; }
.plan-hero p { margin: 0; color: var(--muted); font-size: 12px; }
.plan-metrics { gap: 16px; flex-shrink: 0; }
.plan-metrics span { color: var(--muted); font-size: 10px; text-align: right; }
.plan-metrics b { display: block; color: var(--ink); font: 700 30px/1 var(--font-display); }

.plan-list { display: grid; gap: 14px; margin-top: 20px; }
.plan-card { display: grid; grid-template-columns: 52px 1fr; overflow: hidden; border: 1px solid var(--line); border-radius: 18px; background: linear-gradient(135deg, rgb(255 255 255 / 4%), rgb(255 255 255 / 1%)); }
.order-column { display: grid; grid-template-rows: auto 1fr auto; justify-items: center; gap: 8px; padding: 16px 8px; border-right: 1px solid var(--line); background: rgb(38 235 213 / 3%); }
.order-column > span { color: var(--cyan); font: 700 22px/1 var(--font-display); }
.order-column > i { width: 1px; background: linear-gradient(var(--cyan), transparent); }
.order-column div { display: grid; gap: 5px; }
.order-column button { width: 28px; height: 28px; border: 1px solid var(--line); border-radius: 8px; color: var(--muted); background: transparent; }
.order-column button:disabled { opacity: .2; }

.card-content { min-width: 0; padding: 16px; }
.source-label { margin: 0 0 6px; overflow: hidden; color: var(--muted); font-size: 9px; text-overflow: ellipsis; white-space: nowrap; text-transform: uppercase; letter-spacing: .08em; }
.plan-name { width: 100%; padding: 0; border: 0; color: var(--ink); background: transparent; font: 700 28px/1.1 var(--font-display), var(--font-cn); }
.mode-toggle { display: inline-flex; gap: 4px; margin: 14px 0; padding: 3px; border: 1px solid var(--line); border-radius: 10px; }
.mode-toggle button { padding: 7px 12px; border: 0; border-radius: 7px; color: var(--muted); background: transparent; font-size: 11px; }
.mode-toggle button.active { color: var(--bg); background: var(--cyan); font-weight: 800; }

.parameter-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 9px; }
.parameter-grid label { display: grid; gap: 5px; }
.parameter-grid label > span { display: flex; justify-content: space-between; color: var(--muted); font-size: 10px; }
.parameter-grid em,
.segment-line em { color: var(--cyan); font-size: 9px; font-style: normal; }
.parameter-grid input { width: 100%; padding: 10px; border: 1px solid var(--line); border-radius: 10px; color: var(--ink); background: var(--surface-raised); font: 600 18px/1 var(--font-display); }
.parameter-grid input:focus { border-color: var(--cyan); outline: none; box-shadow: 0 0 0 3px rgb(38 235 213 / 8%); }

.segment-line { gap: 8px; margin-top: 12px; padding: 9px 10px; border-left: 2px solid var(--coral); color: var(--muted); background: rgb(255 111 97 / 5%); font-size: 10px; }
.segment-line b { margin-left: auto; color: var(--ink); font: 600 14px/1 var(--font-display); }
.card-actions { justify-content: flex-end; gap: 8px; margin-top: 14px; }
.card-actions button { padding: 6px 9px; border: 0; color: var(--muted); background: transparent; font-size: 10px; }
.card-actions .danger { color: var(--coral); }

.manual-section { margin-top: 16px; }
.manual-trigger { width: 100%; gap: 13px; padding: 15px; border: 1px dashed var(--line-strong); border-radius: 16px; color: var(--ink); background: transparent; text-align: left; }
.manual-trigger > span { display: grid; width: 34px; height: 34px; place-items: center; border-radius: 50%; color: var(--bg); background: var(--coral); font-size: 22px; }
.manual-trigger strong,
.manual-trigger small { display: block; }
.manual-trigger small { margin-top: 3px; color: var(--muted); }
.manual-form { display: grid; gap: 13px; padding: 18px; border: 1px solid var(--line-strong); border-radius: 18px; background: var(--surface); }
.manual-form h2 { margin: 5px 0 0; }
.manual-form label { display: grid; gap: 6px; color: var(--muted); font-size: 11px; }
.manual-form input { padding: 12px; border: 1px solid var(--line); border-radius: 10px; color: var(--ink); background: var(--surface-raised); }
.manual-form .mode-toggle { margin: 0; justify-self: start; }
.manual-actions { justify-content: flex-end; gap: 8px; }
.manual-actions button { padding: 9px 14px; border: 1px solid var(--line); border-radius: 9px; color: var(--muted); background: transparent; }
.manual-actions .confirm { color: var(--bg); border-color: var(--cyan); background: var(--cyan); font-weight: 800; }

.empty-plan { margin-top: 20px; padding: 48px 20px; border: 1px dashed var(--line); border-radius: 18px; text-align: center; }
.empty-plan > span { color: var(--coral); font: 700 24px/1 var(--font-display); }
.empty-plan h2 { margin: 8px 0; }
.empty-plan p { max-width: 320px; margin: auto; color: var(--muted); font-size: 12px; line-height: 1.7; }

.start-training-panel {
  position: sticky;
  bottom: max(12px, env(safe-area-inset-bottom));
  z-index: 5;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  margin-top: 20px;
  padding: 14px;
  border: 1px solid rgb(38 235 213 / 28%);
  border-radius: 16px;
  background: rgb(16 20 23 / 94%);
  box-shadow: 0 16px 40px rgb(0 0 0 / 38%);
  backdrop-filter: blur(18px);
}
.start-training-panel strong,
.start-training-panel small { display: block; }
.start-training-panel strong { font-size: 12px; }
.start-training-panel small { margin-top: 3px; color: var(--muted); font-size: 9px; }
.start-training-panel button,
.start-training-panel a {
  min-height: 44px;
  padding: 0 18px;
  border: 0;
  border-radius: 12px;
  color: var(--bg);
  background: var(--cyan);
  font-size: 12px;
  font-weight: 800;
  text-decoration: none;
}
.start-training-panel a { display: grid; place-items: center; }
.start-training-panel button:disabled { opacity: .55; }
.plan-error {
  margin-top: 12px;
  padding: 14px;
  border: 1px solid rgb(255 111 97 / 30%);
  border-radius: 14px;
  color: var(--coral);
  background: rgb(255 111 97 / 6%);
}
.plan-error p { margin: 5px 0 0; font-size: 11px; }

@media (min-width: 720px) {
  .plan-page { padding-inline: 28px; }
  .parameter-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }
  .plan-card { grid-template-columns: 62px 1fr; }
}
</style>
