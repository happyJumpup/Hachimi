<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import type { ActionMode, AnalysisCandidate, AnalysisWarning, Segment } from '@/domain/types'

interface EditableCandidate extends AnalysisCandidate {
  selected: boolean
  originalSegment: Segment | null
}

const props = defineProps<{
  candidates: AnalysisCandidate[]
  warnings: AnalysisWarning[]
}>()

const emit = defineEmits<{
  preview: [segment: Segment]
  add: [candidates: AnalysisCandidate[], editedSegmentIds: string[]]
  close: []
}>()

const editable = ref<EditableCandidate[]>([])

const cloneCandidate = (candidate: AnalysisCandidate): AnalysisCandidate => ({
  id: candidate.id,
  name: candidate.name,
  source_id: candidate.source_id,
  segment: candidate.segment ? { ...candidate.segment } : null,
  parameters: { ...candidate.parameters },
  evidence: candidate.evidence.map((evidence) => ({ ...evidence })),
  needs_confirmation: candidate.needs_confirmation,
})

watch(
  () => props.candidates,
  (candidates) => {
    editable.value = candidates.map((candidate) => ({
      ...cloneCandidate(candidate),
      selected: true,
      originalSegment: candidate.segment ? { ...candidate.segment } : null,
    }))
  },
  { immediate: true },
)

const selected = computed(() => editable.value.filter((candidate) => candidate.selected))
const needsMode = computed(() => selected.value.some((candidate) => candidate.parameters.mode === null))
const invalidName = computed(() => selected.value.some((candidate) => !candidate.name.trim()))
const invalidSegment = computed(() =>
  selected.value.some(
    (candidate) =>
      candidate.segment !== null && candidate.segment.end_seconds <= candidate.segment.start_seconds,
  ),
)
const canAdd = computed(
  () => selected.value.length > 0 && !needsMode.value && !invalidName.value && !invalidSegment.value,
)

const formatTime = (seconds: number): string => {
  const minutes = Math.floor(seconds / 60)
  return `${minutes}:${Math.floor(seconds % 60).toString().padStart(2, '0')}`
}

const setMode = (candidate: EditableCandidate, mode: ActionMode): void => {
  candidate.parameters.mode = mode
  if (mode === 'reps') candidate.parameters.duration_seconds = null
  else candidate.parameters.reps = null
}

const isSegmentEdited = (candidate: EditableCandidate): boolean => {
  if (!candidate.segment || !candidate.originalSegment) return candidate.segment !== candidate.originalSegment
  return (
    candidate.segment.start_seconds !== candidate.originalSegment.start_seconds ||
    candidate.segment.end_seconds !== candidate.originalSegment.end_seconds
  )
}

const submit = (): void => {
  if (!canAdd.value) return
  const chosen = selected.value.map((candidate) => cloneCandidate(candidate))
  emit(
    'add',
    chosen,
    selected.value.filter(isSegmentEdited).map((candidate) => candidate.id),
  )
}
</script>

<template>
  <section class="candidate-panel" aria-labelledby="candidate-title">
    <div class="panel-handle" aria-hidden="true" />
    <header class="panel-header">
      <div>
        <p class="eyebrow">ACTION PICK</p>
        <h2 id="candidate-title">找到 {{ editable.length }} 个动作</h2>
      </div>
      <div class="panel-header-actions">
        <span class="review-note">由你确认</span>
        <button
          type="button"
          class="return-button"
          aria-label="返回视频并重新选择时间点"
          @click="emit('close')"
        >
          返回视频
        </button>
      </div>
    </header>

    <p v-for="warning in warnings" :key="warning.code" class="warning-note">
      {{ warning.message }}
    </p>

    <div class="candidate-list">
      <article
        v-for="(candidate, index) in editable"
        :key="candidate.id"
        class="candidate-card"
        :class="{ 'is-muted': !candidate.selected }"
      >
        <label class="candidate-select">
          <input v-model="candidate.selected" type="checkbox" />
          <span class="candidate-index">{{ String(index + 1).padStart(2, '0') }}</span>
          <span>加入草稿</span>
        </label>

        <input
          v-model.trim="candidate.name"
          class="action-name-input"
          aria-label="动作名称"
          :disabled="!candidate.selected"
        />

        <div class="evidence-row">
          <span v-for="evidence in candidate.evidence" :key="`${evidence.type}-${evidence.start_seconds}`">
            {{ evidence.type === 'speech' ? '讲解依据' : '画面依据' }}
          </span>
          <span v-if="candidate.needs_confirmation" class="needs-check">需确认</span>
        </div>

        <div v-if="candidate.segment" class="segment-editor">
          <button type="button" class="preview-button" @click="emit('preview', candidate.segment)">
            <span class="play-mark">▶</span>
            {{ formatTime(candidate.segment.start_seconds) }}—{{ formatTime(candidate.segment.end_seconds) }}
          </button>
          <label>
            起
            <input
              v-model.number="candidate.segment.start_seconds"
              type="number"
              min="0"
              step="0.5"
              :disabled="!candidate.selected"
            />
          </label>
          <label>
            止
            <input
              v-model.number="candidate.segment.end_seconds"
              type="number"
              min="0.5"
              step="0.5"
              :disabled="!candidate.selected"
            />
          </label>
        </div>

        <div class="mode-picker" :class="{ 'needs-choice': candidate.parameters.mode === null }">
          <span>训练方式</span>
          <button
            type="button"
            :class="{ active: candidate.parameters.mode === 'reps' }"
            :disabled="!candidate.selected"
            @click="setMode(candidate, 'reps')"
          >
            按次数
          </button>
          <button
            type="button"
            :class="{ active: candidate.parameters.mode === 'duration' }"
            :disabled="!candidate.selected"
            @click="setMode(candidate, 'duration')"
          >
            按时长
          </button>
        </div>
      </article>
    </div>

    <footer class="panel-footer">
      <p v-if="needsMode">先为选中的动作选择训练方式</p>
      <p v-else-if="invalidName">动作名称不能为空</p>
      <p v-else-if="invalidSegment">结束时间要晚于开始时间</p>
      <p v-else>参数缺失时会使用可修改的规则默认值</p>
      <button type="button" class="primary-action" :disabled="!canAdd" @click="submit">
        加入草稿 · {{ selected.length }}
      </button>
    </footer>
  </section>
</template>

<style scoped>
.candidate-panel {
  position: absolute;
  z-index: 20;
  inset: auto 0 0;
  max-height: min(74dvh, 720px);
  overflow: auto;
  padding: 10px 18px calc(20px + env(safe-area-inset-bottom));
  color: var(--ink);
  background: color-mix(in srgb, var(--surface) 96%, transparent);
  border-top: 1px solid var(--line-strong);
  border-radius: 24px 24px 0 0;
  box-shadow: 0 -24px 60px rgb(0 0 0 / 45%);
  backdrop-filter: blur(22px);
  animation: panel-in 320ms cubic-bezier(.16, 1, .3, 1) both;
}

.panel-handle {
  width: 42px;
  height: 4px;
  margin: 0 auto 14px;
  border-radius: 10px;
  background: var(--line-strong);
}

.panel-header,
.panel-footer,
.candidate-select,
.evidence-row,
.segment-editor,
.mode-picker {
  display: flex;
  align-items: center;
}

.panel-header {
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.panel-header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.panel-header h2 {
  margin: 2px 0 0;
  font-size: 22px;
}

.eyebrow {
  margin: 0;
  color: var(--cyan);
  font: 600 12px/1 var(--font-display);
  letter-spacing: .16em;
}

.review-note,
.evidence-row span {
  padding: 5px 8px;
  border: 1px solid var(--line);
  border-radius: 999px;
  color: var(--muted);
  font-size: 11px;
}

.return-button {
  min-height: 44px;
  padding: 0 12px;
  border: 1px solid var(--line-strong);
  border-radius: 10px;
  color: var(--ink);
  background: transparent;
  font-size: 12px;
  font-weight: 700;
}

.warning-note {
  margin: 8px 0;
  padding: 10px 12px;
  border-left: 2px solid var(--coral);
  color: var(--muted);
  background: rgb(255 111 97 / 8%);
  font-size: 12px;
}

.candidate-list {
  display: grid;
  gap: 10px;
}

.candidate-card {
  padding: 14px;
  border: 1px solid var(--line);
  border-radius: 16px;
  background: linear-gradient(140deg, rgb(255 255 255 / 5%), rgb(255 255 255 / 1%));
  transition: opacity 160ms ease, border-color 160ms ease;
}

.candidate-card:has(input:focus-visible) {
  border-color: var(--cyan);
}

.candidate-card.is-muted {
  opacity: .46;
}

.candidate-select {
  min-height: 44px;
  gap: 8px;
  color: var(--muted);
  font-size: 12px;
}

.candidate-select input {
  accent-color: var(--cyan);
}

.candidate-index {
  color: var(--cyan);
  font: 700 16px/1 var(--font-display);
}

.action-name-input {
  width: 100%;
  min-height: 44px;
  margin: 10px 0 8px;
  padding: 0;
  border: 0;
  color: var(--ink);
  background: transparent;
  font: 700 24px/1.1 var(--font-display), var(--font-cn);
}

.evidence-row {
  flex-wrap: wrap;
  gap: 5px;
  margin-bottom: 12px;
}

.evidence-row .needs-check {
  color: var(--coral);
  border-color: rgb(255 111 97 / 35%);
}

.segment-editor {
  display: grid;
  grid-template-columns: 1fr 76px 76px;
  gap: 8px;
}

.segment-editor label {
  display: grid;
  grid-template-columns: auto 1fr;
  align-items: center;
  gap: 4px;
  color: var(--muted);
  font-size: 11px;
}

.segment-editor input {
  width: 100%;
  min-height: 44px;
  padding: 7px 4px;
  border: 1px solid var(--line);
  border-radius: 8px;
  color: var(--ink);
  background: var(--surface-raised);
  font: 600 15px/1 var(--font-display);
}

.preview-button {
  min-height: 44px;
  padding: 8px 10px;
  border: 1px solid rgb(38 235 213 / 25%);
  border-radius: 9px;
  color: var(--cyan);
  background: rgb(38 235 213 / 7%);
  font: 600 15px/1 var(--font-display);
  text-align: left;
}

.play-mark {
  margin-right: 5px;
  font-size: 9px;
}

.mode-picker {
  gap: 6px;
  margin-top: 12px;
}

.mode-picker > span {
  margin-right: auto;
  color: var(--muted);
  font-size: 12px;
}

.mode-picker button {
  min-width: 44px;
  min-height: 44px;
  padding: 7px 10px;
  border: 1px solid var(--line);
  border-radius: 9px;
  color: var(--muted);
  background: transparent;
}

.mode-picker button.active {
  color: var(--bg);
  border-color: var(--cyan);
  background: var(--cyan);
}

.mode-picker.needs-choice > span {
  color: var(--coral);
}

.panel-footer {
  position: sticky;
  bottom: calc(-20px - env(safe-area-inset-bottom));
  justify-content: space-between;
  gap: 12px;
  margin: 14px -18px calc(-20px - env(safe-area-inset-bottom));
  padding: 14px 18px calc(18px + env(safe-area-inset-bottom));
  border-top: 1px solid var(--line);
  background: var(--surface);
}

.panel-footer p {
  max-width: 180px;
  margin: 0;
  color: var(--muted);
  font-size: 11px;
}

.primary-action {
  min-height: 44px;
  padding: 12px 18px;
  border: 0;
  border-radius: 12px;
  color: #03100e;
  background: var(--cyan);
  box-shadow: 0 8px 24px rgb(38 235 213 / 22%);
  font-weight: 800;
}

.primary-action:disabled {
  color: var(--muted);
  background: var(--line-strong);
  box-shadow: none;
}

@keyframes panel-in {
  from { opacity: 0; transform: translateY(30px); }
}

@media (min-width: 900px) {
  .candidate-panel {
    inset: 22px 22px 22px auto;
    width: min(430px, 42vw);
    max-height: none;
    border: 1px solid var(--line-strong);
    border-radius: 22px;
    box-shadow: -24px 0 60px rgb(0 0 0 / 35%);
  }

  .panel-handle { display: none; }
}

@media (prefers-reduced-motion: reduce) {
  .candidate-panel { animation: none; }
}
</style>
