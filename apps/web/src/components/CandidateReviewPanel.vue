<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import type {
  ActionMode,
  AnalysisCandidate,
  AnalysisWarning,
  CoverageGap,
  Segment,
  SegmentRole,
} from '@/domain/types'

interface EditableCandidate extends Omit<AnalysisCandidate, 'segment'> {
  segment: Segment | null
  selected: boolean
  originalSegment: Segment | null
  originalRole: SegmentRole
}

const props = defineProps<{
  candidates: AnalysisCandidate[]
  warnings: AnalysisWarning[]
  maxSegmentEnd?: number
  submitting?: boolean
  submissionError?: string
  coverageGaps?: CoverageGap[]
  retryingGap?: CoverageGap | null
  gapRetryError?: string | null
}>()

const emit = defineEmits<{
  preview: [segment: Segment]
  add: [candidates: AnalysisCandidate[], editedSegmentIds: string[], editedRoleIds: string[]]
  retryGap: [gap: CoverageGap]
  close: []
}>()

const editable = ref<EditableCandidate[]>([])

const toEditableCandidate = (candidate: AnalysisCandidate): EditableCandidate => {
  const segment = (candidate as AnalysisCandidate & { segment: Segment | null }).segment
  return {
    id: candidate.id,
    name: candidate.name,
    source_id: candidate.source_id,
    segment: segment ? { ...segment } : null,
    parameters: { ...candidate.parameters },
    evidence: candidate.evidence.map((evidence) => ({ ...evidence })),
    needs_confirmation: candidate.needs_confirmation,
    segment_role: candidate.segment_role ?? 'unknown',
    selected: true,
    originalSegment: segment ? { ...segment } : null,
    originalRole: candidate.segment_role ?? 'unknown',
  }
}

const toAnalysisCandidate = (candidate: EditableCandidate): AnalysisCandidate => {
  if (!candidate.segment) throw new Error('selected video candidate requires a segment')
  return {
    id: candidate.id,
    name: candidate.name,
    source_id: candidate.source_id,
    segment: { ...candidate.segment },
    parameters: { ...candidate.parameters },
    evidence: candidate.evidence.map((evidence) => ({ ...evidence })),
    needs_confirmation: candidate.needs_confirmation,
    segment_role: candidate.segment_role ?? 'unknown',
  }
}

watch(
  () => props.candidates,
  (candidates) => {
    const current = new Map(editable.value.map((candidate) => [candidate.id, candidate]))
    editable.value = candidates.map((candidate) => {
      const previous = current.get(candidate.id)
      if (!previous) return toEditableCandidate(candidate)
      const refreshed = toEditableCandidate(candidate)
      return {
        ...refreshed,
        name: previous.name,
        segment: previous.segment ? { ...previous.segment } : null,
        parameters: { ...previous.parameters },
        segment_role: previous.segment_role,
        selected: previous.selected,
        originalSegment: previous.originalSegment ? { ...previous.originalSegment } : null,
        originalRole: previous.originalRole,
      }
    })
  },
  { immediate: true },
)

const selected = computed(() => editable.value.filter((candidate) => candidate.selected))
const needsMode = computed(() => selected.value.some((candidate) => candidate.parameters.mode === null))
const needsRole = computed(() => selected.value.some((candidate) => candidate.segment_role === 'unknown'))
const invalidName = computed(() => selected.value.some((candidate) => !candidate.name.trim()))
const missingSegment = computed(() => selected.value.some((candidate) => candidate.segment === null))
const invalidSegment = computed(() =>
  selected.value.some(
    (candidate) => candidate.segment !== null && (
      !Number.isFinite(candidate.segment.start_seconds)
      || !Number.isFinite(candidate.segment.end_seconds)
      || candidate.segment.start_seconds < 0
      || candidate.segment.end_seconds <= candidate.segment.start_seconds
      || (
        props.maxSegmentEnd !== undefined
        && candidate.segment.end_seconds > props.maxSegmentEnd
      )
    ),
  ),
)
const canAdd = computed(
  () => selected.value.length > 0
    && !needsMode.value
    && !needsRole.value
    && !invalidName.value
    && !missingSegment.value
    && !invalidSegment.value
    && !props.submitting,
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

const gapReason = (reason: CoverageGap['reason']): string => ({
  provider_error: '处理服务暂时出错',
  timeout: '处理超时',
  media_error: '这段媒体暂时无法读取',
  unknown: '这段暂时无法判断',
})[reason]

const isRetryingGap = (gap: CoverageGap): boolean => Boolean(
  props.retryingGap
  && props.retryingGap.start_seconds === gap.start_seconds
  && props.retryingGap.end_seconds === gap.end_seconds,
)

const setRole = (candidate: EditableCandidate, role: Exclude<SegmentRole, 'unknown'>): void => {
  candidate.segment_role = role
}

const isSegmentEdited = (candidate: EditableCandidate): boolean => {
  if (!candidate.segment || !candidate.originalSegment) return candidate.segment !== candidate.originalSegment
  return (
    candidate.segment.start_seconds !== candidate.originalSegment.start_seconds ||
    candidate.segment.end_seconds !== candidate.originalSegment.end_seconds
  )
}

const isRoleEdited = (candidate: EditableCandidate): boolean =>
  candidate.segment_role !== candidate.originalRole

const submit = (): void => {
  if (!canAdd.value) return
  const chosen = selected.value.map(toAnalysisCandidate)
  emit(
    'add',
    chosen,
    selected.value.filter(isSegmentEdited).map((candidate) => candidate.id),
    selected.value.filter(isRoleEdited).map((candidate) => candidate.id),
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
          aria-label="返回视频"
          @click="emit('close')"
        >
          返回视频
        </button>
      </div>
    </header>

    <p v-for="warning in warnings" :key="warning.code" class="warning-note">
      {{ warning.message }}
    </p>

    <section v-if="props.coverageGaps?.length" class="coverage-gaps" aria-label="分析覆盖缺口">
      <div>
        <strong>部分完成</strong>
        <span>可靠候选仍可使用；未完成区间可以单独重试</span>
      </div>
      <article v-for="gap in props.coverageGaps" :key="`${gap.start_seconds}-${gap.end_seconds}`">
        <p>
          <b>{{ formatTime(gap.start_seconds) }}—{{ formatTime(gap.end_seconds) }}</b>
          <span>{{ gapReason(gap.reason) }}</span>
        </p>
        <button
          type="button"
          :disabled="!gap.retryable || Boolean(props.retryingGap)"
          @click="emit('retryGap', gap)"
        >
          {{ isRetryingGap(gap) ? '正在重试…' : gap.retryable ? '重试这段' : '暂不可重试' }}
        </button>
      </article>
      <p v-if="props.gapRetryError" class="gap-retry-error" role="alert">
        {{ props.gapRetryError }}
      </p>
    </section>

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

        <div v-if="candidate.segment_role === 'unknown'" class="role-picker needs-choice">
          <span>片段用途</span>
          <button type="button" :disabled="!candidate.selected" @click="setRole(candidate, 'follow_along')">
            跟练执行
          </button>
          <button type="button" :disabled="!candidate.selected" @click="setRole(candidate, 'teaching_demo')">
            教学演示
          </button>
        </div>
        <p v-else class="role-note">
          <strong>{{ candidate.segment_role === 'follow_along' ? '跟练执行' : '教学演示' }}</strong>
          {{ candidate.segment_role === 'follow_along' ? '可保留视频明确表达的训练节奏' : '片段只用于预览，训练时长不会取自片段长度' }}
        </p>
      </article>
    </div>

    <footer class="panel-footer">
      <p v-if="props.submissionError" role="alert">{{ props.submissionError }}</p>
      <p v-else-if="needsRole">先确认片段用途：跟练执行或教学演示</p>
      <p v-else-if="needsMode">先为选中的动作选择训练方式</p>
      <p v-else-if="invalidName">动作名称不能为空</p>
      <p v-else-if="missingSegment">这个候选缺少可预览片段，请重新分析</p>
      <p v-else-if="invalidSegment">请填写有效时间，结束时间需晚于开始且不超过视频长度</p>
      <p v-else>参数缺失时会使用可修改的规则默认值</p>
      <button type="button" class="primary-action" :disabled="!canAdd" @click="submit">
        {{ props.submitting ? '正在加入草稿…' : `加入草稿 · ${selected.length}` }}
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
.mode-picker,
.role-picker {
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
  font-size: 11px;
}

.mode-picker {
  gap: 6px;
  margin-top: 12px;
}

.coverage-gaps {
  display: grid;
  gap: 8px;
  margin: 8px 0 12px;
  padding: 12px;
  border: 1px solid rgb(255 111 97 / 35%);
  border-radius: 14px;
  background: rgb(255 111 97 / 7%);
}

.coverage-gaps > div,
.coverage-gaps article {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.coverage-gaps > div { align-items: flex-start; flex-direction: column; gap: 3px; }
.coverage-gaps > div strong { color: var(--coral); }
.coverage-gaps > div span,
.coverage-gaps article span { color: var(--muted); font-size: 11px; }
.coverage-gaps article { padding-top: 8px; border-top: 1px solid var(--line); }
.coverage-gaps article p { display: grid; gap: 2px; margin: 0; }
.coverage-gaps article b { font: 700 15px/1 var(--font-display); }
.coverage-gaps article button { min-height: 44px; padding: 0 10px; border: 1px solid var(--line-strong); border-radius: 9px; color: var(--coral); background: transparent; font-weight: 700; }
.coverage-gaps article button:disabled { color: var(--muted); opacity: .6; }
.gap-retry-error { margin: 0; color: var(--coral); font-size: 11px; }

.role-picker {
  gap: 6px;
  margin-top: 10px;
}

.role-picker > span {
  margin-right: auto;
  color: var(--coral);
  font-size: 12px;
}

.role-picker button {
  min-height: 44px;
  padding: 7px 10px;
  border: 1px solid rgb(255 111 97 / 35%);
  border-radius: 9px;
  color: var(--ink);
  background: transparent;
}

.role-note {
  margin: 10px 0 0;
  color: var(--muted);
  font-size: 11px;
  line-height: 1.5;
}

.role-note strong { color: var(--cyan); }

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
