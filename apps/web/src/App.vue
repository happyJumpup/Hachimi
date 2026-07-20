<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { useTrainingStore } from '@/stores/training'

const route = useRoute()
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
    <RouterView v-slot="{ Component }">
      <Transition name="page" mode="out-in">
        <component :is="Component" />
      </Transition>
    </RouterView>
    <RouterLink
      v-if="training.hasCurrent && route.name !== 'training'"
      class="global-training-entry"
      to="/training"
    >
      <span>{{ continueLabel }}</span>
      <b>→</b>
    </RouterLink>
  </div>
</template>

<style scoped>
.app-shell {
  min-height: 100dvh;
}

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
