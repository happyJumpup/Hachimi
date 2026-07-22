<script setup lang="ts">
import { computed } from 'vue'

import { useDraftStore } from '@/stores/draft'
import { useLibraryStore } from '@/stores/library'

const draft = useDraftStore()
const library = useLibraryStore()

const profileSummary = computed(() => {
  const profile = library.profile
  const entries = [
    profile.age ? `${profile.age} 岁` : null,
    profile.heightCm ? `${profile.heightCm} cm` : null,
    profile.weightKg ? `${profile.weightKg} kg` : null,
  ].filter(Boolean)
  return entries.length ? entries.join(' · ') : '未填写（不影响使用）'
})
</script>

<template>
  <main class="personalize-page tp-page tp-page--immersive">
    <header class="flow-header">
      <RouterLink to="/plan" aria-label="返回训练方案">←</RouterLink>
      <span>让 TrainPal 了解你</span>
      <span aria-hidden="true" />
    </header>

    <section class="intro">
      <p class="tp-kicker">PERSONALIZE · PREVIEW</p>
      <h1 class="tp-title">调整这一次，<br />不是替你做决定。</h1>
      <p class="tp-lead">
        个性化会在保留视频动作的前提下，给组数、次数、时长和休息提供参考。你的手动调整始终优先。
      </p>
    </section>

    <section class="pending-note" role="status">
      <span aria-hidden="true">!</span>
      <div>
        <strong>这版不生成假个性化结果</strong>
        <p>GYMTI 问卷和小猫教练风格仍在定稿；入口与信息层级先按冻结合同落位。</p>
      </div>
    </section>

    <ol class="context-steps" aria-label="TrainPal 个性化上下文">
      <li class="step-card tp-card">
        <span class="step-number">01</span>
        <div>
          <small>GYMTI 健身目标</small>
          <h2>你更想从训练中获得什么</h2>
          <p>目标导向问卷正在定稿，将同时采集训练经验，不会把身体信息推断为健身人格。</p>
        </div>
        <b>待定稿</b>
      </li>
      <li class="step-card tp-card">
        <span class="step-number">02</span>
        <div>
          <small>小猫教练风格</small>
          <h2>选择你喜欢的陪伴方式</h2>
          <p>不同品种对应沟通语气、鼓励方式和提示密度，不会改变动作或训练强度。</p>
        </div>
        <b>形象制作中</b>
      </li>
      <li class="step-card tp-card">
        <span class="step-number">03</span>
        <div>
          <small>可选个人信息</small>
          <h2>{{ profileSummary }}</h2>
          <p>年龄、性别、身高和体重主要用于卡路里约值，只能作为个性策略的弱参考。</p>
          <RouterLink to="/mine">在“我的”中管理</RouterLink>
        </div>
        <b>可跳过</b>
      </li>
      <li class="step-card signal-card tp-card">
        <span class="step-number">04</span>
        <div>
          <small>用户轻反馈</small>
          <h2>以后根据真实完成感受更新</h2>
          <p>“太累、刚好、太轻”等结构化反馈只关联具体动作和场次，不生成黑盒用户画像。</p>
        </div>
        <b>训练后积累</b>
      </li>
    </ol>

    <section class="policy-card tp-card">
      <p class="tp-kicker">HOW IT WORKS</p>
      <h2>个性策略的四部分</h2>
      <p>GYMTI 健身目标 × 小猫教练风格 × 可选个人信息 × 用户轻反馈</p>
      <ul>
        <li>本次视频意图优先，不删除或替换你选中的动作。</li>
        <li>视频明确值默认保留，调整时必须解释原因。</li>
        <li>重量只由你填写，所有建议都可再次修改。</li>
      </ul>
    </section>

    <footer class="flow-action">
      <div>
        <strong>{{ draft.plan.name }}</strong>
        <small>基础方案仍可直接训练</small>
      </div>
      <RouterLink class="tp-primary-action" to="/plan">继续使用基础方案</RouterLink>
    </footer>
  </main>
</template>

<style scoped>
.personalize-page { display: grid; align-content: start; gap: 22px; max-width: 780px; padding-bottom: calc(116px + var(--tp-task-reserve, 0px) + env(safe-area-inset-bottom)); }
.flow-header { display: grid; grid-template-columns: 44px 1fr 44px; align-items: center; }
.flow-header a { display: grid; width: 44px; height: 44px; place-items: center; border: 1px solid var(--tp-line); border-radius: 50%; color: var(--tp-ink); background: var(--tp-surface); font-size: 22px; text-decoration: none; }
.flow-header > span { color: var(--tp-muted); font-size: 13px; font-weight: 800; text-align: center; }
.intro { display: grid; gap: 15px; padding: 20px 0 4px; }
.intro .tp-lead { max-width: 610px; }
.pending-note { display: flex; gap: 12px; padding: 15px; border: 1px solid rgb(154 91 19 / 22%); border-radius: 16px; color: var(--tp-ink); background: #FBF0DA; }
.pending-note > span { display: grid; width: 30px; height: 30px; flex: 0 0 auto; place-items: center; border-radius: 50%; color: #FFFDF8; background: var(--tp-warning); font: 800 18px/1 var(--font-display); }
.pending-note strong { font-size: 13px; }
.pending-note p { margin: 5px 0 0; color: #565F59; font-size: 12px; line-height: 1.6; }

.context-steps { display: grid; gap: 10px; margin: 0; padding: 0; list-style: none; }
.step-card { display: grid; grid-template-columns: 42px minmax(0, 1fr); gap: 5px 12px; padding: 16px; box-shadow: none; }
.step-number { grid-row: 1 / span 2; color: var(--tp-primary); font: 700 24px/1 var(--font-display); }
.step-card small { color: var(--tp-primary-readable); font: 700 11px/1 var(--font-display), var(--font-cn); letter-spacing: .08em; }
.step-card h2 { margin: 5px 0 4px; font-size: 17px; }
.step-card p { margin: 0; color: var(--tp-muted); font-size: 12px; line-height: 1.65; }
.step-card a { display: inline-flex; min-height: 44px; align-items: center; color: var(--tp-primary-readable); font-size: 12px; font-weight: 800; }
.step-card > b { grid-column: 2; justify-self: start; padding: 5px 8px; border-radius: 999px; color: #535C56; background: #F0ECE2; font-size: 11px; }
.signal-card { border-style: dashed; background: var(--tp-surface); }

.policy-card { display: grid; gap: 10px; padding: 20px; background: var(--tp-training-surface); }
.policy-card .tp-kicker { color: var(--tp-secondary); }
.policy-card h2 { margin: 0; color: var(--tp-training-ink); font-size: 23px; }
.policy-card > p:not(.tp-kicker) { margin: 0; color: #D0D6D1; font-size: 13px; line-height: 1.6; }
.policy-card ul { display: grid; gap: 8px; margin: 2px 0 0; padding-left: 18px; color: #B4BDB6; font-size: 12px; line-height: 1.6; }

.flow-action { position: fixed; right: max(14px, env(safe-area-inset-right)); bottom: max(14px, env(safe-area-inset-bottom)); left: max(14px, env(safe-area-inset-left)); z-index: 20; display: flex; max-width: 750px; align-items: center; justify-content: space-between; gap: 12px; margin: auto; padding: 11px 11px 11px 16px; border: 1px solid rgb(28 40 34 / 14%); border-radius: 22px; background: var(--tp-surface); box-shadow: var(--tp-shadow-float); }
.flow-action strong,
.flow-action small { display: block; }
.flow-action strong { max-width: 220px; overflow: hidden; font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.flow-action small { margin-top: 3px; color: var(--tp-muted); font-size: 11px; }
.flow-action .tp-primary-action { flex: 0 0 auto; }

@media (min-width: 700px) {
  .context-steps { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}

@media (max-width: 379px) {
  .flow-action > div { display: none; }
  .flow-action .tp-primary-action { width: 100%; }
}
</style>
