<script setup lang="ts">
import { ref } from 'vue'

import { useAccessStore } from '@/stores/access'

const access = useAccessStore()
const expanded = ref(false)
const code = ref('')

const submit = async (): Promise<void> => {
  if (await access.upgrade(code.value)) {
    code.value = ''
    expanded.value = false
  }
}
</script>

<template>
  <section class="access-status" :class="`tier-${access.tier}`" aria-label="实时 AI 访问状态">
    <div class="access-summary">
      <span class="access-dot" />
      <p>
        <strong>{{ access.message }}</strong>
        <small v-if="access.tier === 'public'">评委可输入共享体验码使用预留通道</small>
        <small v-else>体验码只保存在安全 Cookie 中</small>
      </p>
      <button
        v-if="access.tier === 'public'"
        type="button"
        :aria-expanded="expanded"
        @click="expanded = !expanded"
      >
        {{ expanded ? '收起' : '评委入口' }}
      </button>
    </div>

    <form v-if="expanded && access.tier === 'public'" @submit.prevent="submit">
      <label>
        <span>评委体验码</span>
        <input
          v-model="code"
          type="password"
          autocomplete="one-time-code"
          aria-label="评委体验码"
          placeholder="输入共享体验码"
        />
      </label>
      <button type="submit" :disabled="!code.trim() || access.pending">
        {{ access.pending ? '正在校验…' : '启用预留通道' }}
      </button>
    </form>

    <div v-if="!access.canAnalyze && access.loaded" class="access-fallback">
      <RouterLink to="/mine">使用快速体验方案</RouterLink>
      <button type="button" :disabled="access.pending" @click="access.load()">刷新名额</button>
    </div>
    <p v-if="access.errorMessage" class="access-error" role="alert">{{ access.errorMessage }}</p>
  </section>
</template>

<style scoped>
.access-status { width: min(100%, 430px); margin: 0 auto 10px; padding: 10px 12px; border: 1px solid var(--line); border-radius: 13px; background: rgb(16 20 23 / 86%); }
.access-summary { display: flex; align-items: center; gap: 9px; }
.access-dot { width: 7px; height: 7px; flex: 0 0 auto; border-radius: 50%; background: var(--coral); box-shadow: 0 0 0 4px rgb(255 111 97 / 10%); }
.tier-judge .access-dot { background: var(--cyan); box-shadow: 0 0 0 4px rgb(38 235 213 / 10%); }
.access-summary p { min-width: 0; flex: 1; margin: 0; }
.access-summary strong,
.access-summary small { display: block; }
.access-summary strong { font-size: 10px; }
.access-summary small { margin-top: 2px; color: var(--muted); font-size: 8px; }
.access-summary button,
.access-fallback button { min-height: 44px; padding: 0 7px; border: 0; color: var(--cyan); background: transparent; font-size: 9px; }
.access-status form { display: grid; grid-template-columns: 1fr auto; gap: 8px; margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--line); }
.access-status form label { display: grid; gap: 4px; color: var(--muted); font-size: 8px; }
.access-status form input { min-width: 0; min-height: 44px; padding: 0 10px; border: 1px solid var(--line); border-radius: 9px; color: var(--ink); background: var(--surface-raised); }
.access-status form > button { min-height: 44px; align-self: end; padding: 0 11px; border: 0; border-radius: 9px; color: var(--bg); background: var(--cyan); font-size: 9px; font-weight: 800; }
.access-status form > button:disabled { opacity: .5; }
.access-fallback { display: flex; align-items: center; justify-content: space-between; margin-top: 8px; border-top: 1px solid var(--line); }
.access-fallback a { color: var(--coral); font-size: 9px; font-weight: 700; text-decoration: none; }
.access-error { margin: 6px 0 0; color: var(--coral); font-size: 9px; }
</style>
