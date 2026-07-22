<script setup lang="ts">
import {
  COACH_ACTIONS,
  COACH_STYLE_IDS,
  COACH_STYLE_LABELS,
} from '@/domain/coach'
import CoachMotion from '@/features/experience/CoachMotion.vue'
</script>

<template>
  <main class="tp-page pet-lab">
    <header>
      <p class="tp-kicker">DEV ONLY · ANIMATION ACCEPTANCE</p>
      <h1 class="tp-title">TrainPal<br>七猫动画预览台</h1>
      <p class="tp-lead">
        这里逐项循环原始动作，仅用于开发验收；不会选择、应用或保存任何教练风格。
      </p>
    </header>

    <section
      v-for="(styleId, styleIndex) in COACH_STYLE_IDS"
      :key="styleId"
      class="style-section"
      data-testid="pet-style"
      :aria-labelledby="`style-${styleId}`"
    >
      <header>
        <span>{{ String(styleIndex + 1).padStart(2, '0') }}</span>
        <div>
          <p>{{ styleId }}</p>
          <h2 :id="`style-${styleId}`">{{ COACH_STYLE_LABELS[styleId] }}</h2>
        </div>
      </header>

      <div class="action-grid">
        <article
          v-for="action in COACH_ACTIONS[styleId]"
          :key="action"
          class="action-card tp-card"
          data-testid="pet-action"
        >
          <CoachMotion
            :style-id="styleId"
            state="idle"
            :preview-action="action"
          />
          <div>
            <strong>{{ action }}</strong>
            <small>6 FRAME · WEBP</small>
          </div>
        </article>
      </div>
    </section>
  </main>
</template>

<style scoped>
.pet-lab { display: grid; gap: 52px; max-width: 1180px; padding-bottom: 64px; }
.pet-lab > header { display: grid; gap: 16px; }
.style-section { display: grid; gap: 18px; }
.style-section > header { display: flex; align-items: end; gap: 14px; padding-bottom: 12px; border-bottom: 1px solid var(--tp-line); }
.style-section > header > span { color: var(--tp-primary); font: 700 30px/1 var(--font-display); }
.style-section p { margin: 0 0 4px; color: var(--tp-muted); font: 700 11px/1 var(--font-display); letter-spacing: .12em; text-transform: uppercase; }
.style-section h2 { margin: 0; font: 700 28px/1 var(--font-display), var(--font-cn); }
.action-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.action-card { display: grid; min-width: 0; justify-items: center; gap: 8px; padding: 14px 10px; box-shadow: none; text-align: center; }
.action-card :deep(.coach-motion__image) { width: clamp(96px, 28vw, 156px); }
.action-card strong { display: block; color: var(--tp-ink); font: 700 16px/1 var(--font-display); }
.action-card small { display: block; margin-top: 5px; color: var(--tp-muted); font: 700 11px/1 var(--font-display); letter-spacing: .1em; }

@media (min-width: 720px) {
  .action-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }
}
</style>
