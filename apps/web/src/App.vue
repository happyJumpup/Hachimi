<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { useAppBootstrapStore } from '@/stores/app-bootstrap'
import { useTrainingStore } from '@/stores/training'

const route = useRoute()
const bootstrap = useAppBootstrapStore()
const training = useTrainingStore()
const nowMilliseconds = ref(Date.now())
let ticker: ReturnType<typeof setInterval> | null = null

const continueLabel = computed(() => {
  const current = training.session
  if (!current) return ''
  if (current.status === 'resting' && current.restEndsAt) {
    const remaining = Math.max(0, Math.ceil(
      (Date.parse(current.restEndsAt) - nowMilliseconds.value) / 1_000,
    ))
    if (remaining === 0) return '休息结束 · 准备继续'
    return `休息中 · ${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, '0')}`
  }
  if (current.status === 'ready_to_continue') return '准备继续训练'
  return '继续训练'
})

onMounted(() => {
  ticker = setInterval(() => { nowMilliseconds.value = Date.now() }, 1_000)
})

onBeforeUnmount(() => {
  if (ticker) clearInterval(ticker)
})
</script>

<template>
  <div class="app-shell">
    <main v-if="bootstrap.status !== 'ready'" class="bootstrap-shell">
      <template v-if="bootstrap.status === 'loading'">
        <p class="bootstrap-eyebrow">LOCAL TRAINING DATA</p>
        <h1>正在读取本机训练数据</h1>
        <p>草稿、训练进度和记录只保存在当前设备。</p>
      </template>
      <section v-else role="alert" aria-live="assertive">
        <p class="bootstrap-eyebrow">READ FAILED</p>
        <h1>本机训练数据暂时无法读取</h1>
        <p>数据没有被清除，可以重新尝试读取。</p>
        <button type="button" @click="bootstrap.retry">重试读取</button>
      </section>
    </main>
    <template v-else>
      <RouterView v-slot="{ Component }">
        <Transition name="page" mode="out-in">
          <component :is="Component" />
        </Transition>
      </RouterView>
      <RouterLink
        v-if="training.hasCurrent && !['training', 'plan', 'mine'].includes(String(route.name))"
        class="global-training-entry"
        to="/training"
      >
        <span>{{ continueLabel }}</span>
        <b>→</b>
      </RouterLink>
    </template>
  </div>
</template>

<style scoped>
.app-shell {
  min-height: 100dvh;
}

.bootstrap-shell {
  display: grid;
  min-height: 100dvh;
  place-content: center;
  gap: 10px;
  padding: 24px;
  text-align: center;
}

.bootstrap-shell section { display: grid; gap: 10px; }
.bootstrap-eyebrow { margin: 0; color: var(--cyan); font: 600 11px/1 var(--font-display); letter-spacing: .14em; }
.bootstrap-shell h1 { max-width: 340px; margin: 0; font: 700 42px/.95 var(--font-display), var(--font-cn); }
.bootstrap-shell p:not(.bootstrap-eyebrow) { max-width: 320px; margin: 0 auto; color: var(--muted); font-size: 11px; line-height: 1.7; }
.bootstrap-shell button { min-height: 46px; margin-top: 8px; border: 0; border-radius: 12px; color: var(--bg); background: var(--cyan); font-weight: 800; }

.global-training-entry {
  position: fixed;
  right: max(14px, env(safe-area-inset-right));
  bottom: max(14px, env(safe-area-inset-bottom));
  z-index: 50;
  display: flex;
  min-height: 44px;
  align-items: center;
  gap: 16px;
  padding: 0 14px 0 16px;
  border: 1px solid rgb(38 235 213 / 35%);
  border-radius: 999px;
  color: var(--ink);
  background: rgb(16 20 23 / 94%);
  box-shadow: 0 12px 32px rgb(0 0 0 / 42%);
  font-size: 11px;
  font-weight: 700;
  text-decoration: none;
  backdrop-filter: blur(16px);
}

.global-training-entry b { color: var(--cyan); font-size: 18px; }

.page-enter-active,
.page-leave-active {
  transition: opacity 180ms ease, transform 220ms cubic-bezier(.2, .8, .2, 1);
}

.page-enter-from {
  opacity: 0;
  transform: translateY(10px);
}

.page-leave-to {
  opacity: 0;
  transform: translateY(-6px);
}

@media (prefers-reduced-motion: reduce) {
  .page-enter-active,
  .page-leave-active {
    transition: none;
  }
}
</style>
