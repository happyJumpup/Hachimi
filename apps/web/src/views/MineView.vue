<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import type { TrainingProfile } from '@/domain/training'
import ProfileForm from '@/features/experience/ProfileForm.vue'
import { QUICK_EXPERIENCE_LABEL } from '@/features/quick-experience/fixture'
import { useDraftStore } from '@/stores/draft'
import { useLibraryStore } from '@/stores/library'
import { useTrainingStore } from '@/stores/training'

const router = useRouter()
const draft = useDraftStore()
const library = useLibraryStore()
const training = useTrainingStore()
const pending = ref(false)
const notice = ref('')

const currentDraftLabel = computed(() =>
  draft.items.length ? `${draft.items.length} 个动作` : '还没有动作',
)

const formatDate = (value: string): string => new Intl.DateTimeFormat('zh-CN', {
  month: 'numeric',
  day: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
}).format(new Date(value))

const useQuickPlan = async (): Promise<void> => {
  if (pending.value) return
  pending.value = true
  try {
    const next = await library.useQuickExperience()
    draft.adoptPersistedPlan(next)
    await router.push('/plan')
  } finally {
    pending.value = false
  }
}

const openPlan = async (planId: string): Promise<void> => {
  if (pending.value) return
  pending.value = true
  try {
    draft.adoptPersistedPlan(await library.openPlan(planId))
    await router.push('/plan')
  } finally {
    pending.value = false
  }
}

const saveProfile = async (
  profile: Omit<TrainingProfile, 'id' | 'updatedAt'>,
): Promise<void> => {
  await library.saveProfile(profile)
  notice.value = '训练档案已保存到本机'
}

const clearProfile = async (): Promise<void> => {
  await library.clearProfile()
  notice.value = '训练档案已清除'
}

const clearEverything = async (): Promise<void> => {
  if (!window.confirm('将清除本机上的草稿、方案、未完成训练、记录和训练档案。确定继续吗？')) return
  await library.clearAllLocalData()
  draft.resetLocalState()
  training.resetLocalState()
  notice.value = '本机训练数据已清除'
}

onMounted(() => library.refreshHistory())
</script>

<template>
  <main class="mine-page">
    <header class="mine-header">
      <RouterLink to="/">← 返回视频</RouterLink>
      <span>只保存在当前设备</span>
    </header>

    <section class="mine-hero">
      <p class="eyebrow">MY TRAINING</p>
      <h1>我的训练</h1>
      <p>方案、进度和记录都在这里。</p>
    </section>

    <p v-if="notice" class="notice" role="status">{{ notice }}</p>

    <section class="quick-card">
      <div>
        <span>{{ QUICK_EXPERIENCE_LABEL }}</span>
        <h2>8 分钟手臂唤醒</h2>
        <p>无需等待分析，先走通训练、Pet 和结果流程。</p>
      </div>
      <button type="button" :disabled="pending" @click="useQuickPlan">使用快速体验方案</button>
      <small>这是产品示例，不是 AI 分析结果。</small>
    </section>

    <section class="dashboard-grid">
      <RouterLink class="dashboard-card" to="/plan">
        <span>当前草稿</span>
        <strong>{{ draft.plan.name }}</strong>
        <small>{{ currentDraftLabel }} · 继续编辑 →</small>
      </RouterLink>
      <RouterLink v-if="training.hasCurrent" class="dashboard-card active-session" to="/training">
        <span>未完成训练</span>
        <strong>{{ training.session?.plan.name }}</strong>
        <small>继续当前进度 →</small>
      </RouterLink>
    </section>

    <section class="mine-section">
      <div class="section-heading">
        <div><span>PLANS</span><h2>已存方案</h2></div>
        <b>{{ library.plans.length }}</b>
      </div>
      <div v-if="library.plans.length" class="row-list">
        <button v-for="plan in library.plans" :key="plan.id" type="button" @click="openPlan(plan.id)">
          <span><strong>{{ plan.name }}</strong><small>{{ plan.items.length }} 个动作 · {{ formatDate(plan.updatedAt) }}</small></span>
          <b>编辑 →</b>
        </button>
      </div>
      <p v-else class="section-empty">在方案页使用“另存为”，这里就会出现可复用方案。</p>
    </section>

    <section class="mine-section">
      <div class="section-heading">
        <div><span>RECORDS</span><h2>训练记录</h2></div>
        <b>{{ library.records.length }}</b>
      </div>
      <div v-if="library.records.length" class="row-list">
        <RouterLink v-for="record in library.records" :key="record.id" :to="`/result/${record.id}`">
          <span><strong>{{ record.plan.name }}</strong><small>{{ formatDate(record.endedAt) }} · 约 {{ record.calorie.value }} 千卡</small></span>
          <b>{{ record.outcome === 'completed' ? '已完成' : '提前结束' }}</b>
        </RouterLink>
      </div>
      <p v-else class="section-empty">完成或提前结束一次训练后，实际结果会保存在这里。</p>
    </section>

    <section class="mine-section profile-section">
      <div class="section-heading">
        <div><span>PROFILE</span><h2>训练档案</h2></div>
      </div>
      <p class="section-copy">用于卡路里约值；不包含体脂率，也不会上传。</p>
      <ProfileForm :profile="library.profile" @save="saveProfile" @clear="clearProfile" />
    </section>

    <section class="mine-section preference-row">
      <div><span>PET</span><h2>训练中显示哈肌咪</h2></div>
      <button
        type="button"
        :aria-pressed="library.preferences.petVisible"
        @click="library.setPetVisible(!library.preferences.petVisible)"
      >
        {{ library.preferences.petVisible ? '已显示' : '已隐藏' }}
      </button>
    </section>

    <button type="button" class="clear-data" @click="clearEverything">清除本机训练数据</button>
  </main>
</template>

<style scoped>
.mine-page { width: min(100%, 780px); min-height: 100dvh; margin: auto; padding: max(18px, env(safe-area-inset-top)) 16px max(40px, env(safe-area-inset-bottom)); }
.mine-header,
.section-heading,
.preference-row,
.row-list button,
.row-list a { display: flex; align-items: center; }
.mine-header { justify-content: space-between; }
.mine-header a { color: var(--ink); font-size: 11px; font-weight: 700; text-decoration: none; }
.mine-header > span { color: var(--muted); font-size: 9px; }
.mine-hero { padding: 42px 0 24px; border-bottom: 1px solid var(--line); }
.eyebrow,
.section-heading span,
.preference-row span { margin: 0; color: var(--cyan); font: 600 10px/1 var(--font-display); letter-spacing: .14em; }
.mine-hero h1 { margin: 8px 0 4px; font: 700 clamp(46px, 14vw, 72px)/.9 var(--font-display), var(--font-cn); }
.mine-hero > p:last-child,
.section-copy { margin: 0; color: var(--muted); font-size: 11px; }
.notice { margin: 14px 0 0; padding: 10px; border-radius: 10px; color: var(--cyan); background: rgb(38 235 213 / 7%); font-size: 11px; }
.quick-card { display: grid; gap: 12px; margin-top: 18px; padding: 18px; border: 1px solid rgb(255 111 97 / 35%); border-radius: 20px; background: linear-gradient(135deg, rgb(255 111 97 / 10%), rgb(38 235 213 / 4%)); }
.quick-card span { color: var(--coral); font: 700 10px/1 var(--font-display); letter-spacing: .12em; }
.quick-card h2 { margin: 5px 0; font: 700 30px/1 var(--font-display), var(--font-cn); }
.quick-card p,
.quick-card small { margin: 0; color: var(--muted); font-size: 10px; line-height: 1.6; }
.quick-card button { min-height: 46px; border: 0; border-radius: 12px; color: var(--bg); background: var(--coral); font-weight: 800; }
.dashboard-grid { display: grid; gap: 10px; margin-top: 12px; }
.dashboard-card { display: grid; gap: 5px; padding: 16px; border: 1px solid var(--line); border-radius: 16px; color: var(--ink); background: var(--surface); text-decoration: none; }
.dashboard-card span,
.dashboard-card small { color: var(--muted); font-size: 9px; }
.dashboard-card strong { font-size: 16px; }
.active-session { border-color: rgb(38 235 213 / 35%); }
.mine-section { margin-top: 24px; padding: 18px; border: 1px solid var(--line); border-radius: 18px; background: rgb(255 255 255 / 2%); }
.section-heading { justify-content: space-between; }
.section-heading h2,
.preference-row h2 { margin: 4px 0 0; font-size: 18px; }
.section-heading > b { color: var(--muted); font: 700 28px/1 var(--font-display); }
.row-list { display: grid; margin-top: 12px; }
.row-list button,
.row-list a { width: 100%; justify-content: space-between; gap: 12px; min-height: 58px; padding: 10px 0; border: 0; border-top: 1px solid var(--line); color: var(--ink); background: transparent; text-align: left; text-decoration: none; }
.row-list span { min-width: 0; }
.row-list strong,
.row-list small { display: block; }
.row-list small { margin-top: 4px; color: var(--muted); font-size: 9px; }
.row-list b { color: var(--cyan); font-size: 10px; white-space: nowrap; }
.section-empty { margin: 14px 0 0; color: var(--muted); font-size: 10px; line-height: 1.7; }
.profile-section { display: grid; gap: 12px; }
.preference-row { justify-content: space-between; }
.preference-row button { min-width: 74px; min-height: 44px; border: 1px solid var(--line); border-radius: 999px; color: var(--cyan); background: var(--surface-raised); }
.clear-data { width: 100%; min-height: 44px; margin-top: 24px; border: 1px solid rgb(255 111 97 / 28%); border-radius: 12px; color: var(--coral); background: transparent; }
@media (min-width: 640px) { .dashboard-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
</style>
