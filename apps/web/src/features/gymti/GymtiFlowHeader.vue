<script setup lang="ts">
defineProps<{
  title?: string
  label: string
  backLabel: string
  locked?: boolean
}>()

defineEmits<{ back: [] }>()
</script>

<template>
  <header class="gymti-flow-header">
    <button type="button" :aria-label="backLabel" :disabled="locked" @click="$emit('back')">←</button>
    <strong>{{ title ?? 'GYMTI' }}</strong>
    <span>{{ label }}</span>
  </header>
</template>

<style scoped>
.gymti-flow-header { display: grid; grid-template-columns: 44px minmax(0, 1fr) minmax(64px, auto); min-height: 44px; align-items: center; gap: 10px; }
.gymti-flow-header button { width: 44px; min-height: 44px; padding: 0; border: 1px solid var(--tp-line); border-radius: 50%; color: var(--tp-ink); background: var(--tp-surface); font-size: 21px; }
.gymti-flow-header strong { justify-self: center; font: 700 15px/1 var(--font-display), var(--font-cn); letter-spacing: .08em; }
.gymti-flow-header span { color: var(--tp-muted); font-size: 12px; font-weight: 800; text-align: right; }
</style>
