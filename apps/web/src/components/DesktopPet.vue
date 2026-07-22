<script setup lang="ts">
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  reactive,
  ref,
  watch,
} from 'vue'

import {
  getPetActionDefinition,
  getPetActions,
  getPetDefinition,
  getPetFrame,
  PET_ACTION_LABELS,
  resolvePetAction,
  type PetAction,
  type PetCue,
  type PetId,
} from '@/domain/pet'

interface PetPosition {
  x: number
  y: number
}

interface DragSession {
  pointerId: number
  originX: number
  originY: number
  startX: number
  startY: number
  moved: boolean
}

const props = withDefaults(defineProps<{
  petId?: PetId
  cue?: PetCue
  action?: PetAction
  visible?: boolean
  draggable?: boolean
  persistKey?: string
  width?: number
}>(), {
  petId: 'hotblood',
  cue: 'idle',
  visible: true,
  draggable: true,
  persistKey: 'hachimi.desktop-pet.position.v1',
  width: 164,
})

const emit = defineEmits<{
  'asset-error': [url: string]
  'position-change': [position: PetPosition]
}>()

const petElement = ref<HTMLElement | null>(null)
const frameIndex = ref(0)
const isDragging = ref(false)
const isReady = ref(false)
const assetAvailable = ref(true)
const prefersReducedMotion = ref(false)
const position = reactive<PetPosition>({ x: 0, y: 0 })

const requestedAction = computed<PetAction>(() => resolvePetAction(props.petId, props.cue, props.action))
const activeAction = computed<PetAction>(() => isDragging.value
  ? resolvePetAction(props.petId, 'drag')
  : requestedAction.value)
const frameUrl = computed(() => getPetFrame(props.petId, activeAction.value, frameIndex.value))
const petLabel = computed(() => {
  const pet = getPetDefinition(props.petId)
  return `${pet.shortName}，当前动作：${PET_ACTION_LABELS[activeAction.value]}`
})
const petStyle = computed(() => ({
  '--pet-width': `${props.width}px`,
  transform: `translate3d(${position.x}px, ${position.y}px, 0)`,
}))

let animationFrameId = 0
let lastFrameAt = 0
let dragSession: DragSession | null = null
let motionQuery: MediaQueryList | null = null

function markWindowMoved(value: boolean) {
  const applicationWindow = window as Window & { isMoved?: boolean }
  applicationWindow.isMoved = value
}

function elementSize(): { width: number, height: number } {
  const bounds = petElement.value?.getBoundingClientRect()
  return {
    width: bounds?.width || props.width,
    height: bounds?.height || props.width * (288 / 256),
  }
}

function clampPosition(candidate: PetPosition): PetPosition {
  const margin = 8
  const size = elementSize()
  return {
    x: Math.min(Math.max(candidate.x, margin), Math.max(margin, window.innerWidth - size.width - margin)),
    y: Math.min(Math.max(candidate.y, margin), Math.max(margin, window.innerHeight - size.height - margin)),
  }
}

function applyPosition(candidate: PetPosition) {
  const next = clampPosition(candidate)
  position.x = next.x
  position.y = next.y
}

function defaultPosition(): PetPosition {
  const size = elementSize()
  return clampPosition({
    x: window.innerWidth - size.width - 20,
    y: window.innerHeight - size.height - 24,
  })
}

function readStoredPosition(): PetPosition | null {
  try {
    let raw = window.localStorage.getItem(props.persistKey)
    if (!raw && props.persistKey === 'hachimi.desktop-pet.position.v1') {
      raw = window.localStorage.getItem('hachimi.desktop-pet.hotblood.position.v1')
      if (raw) window.localStorage.setItem(props.persistKey, raw)
    }
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<PetPosition>
    if (!Number.isFinite(parsed.x) || !Number.isFinite(parsed.y)) return null
    return { x: Number(parsed.x), y: Number(parsed.y) }
  } catch {
    return null
  }
}

function savePosition() {
  const value = { x: Math.round(position.x), y: Math.round(position.y) }
  try {
    window.localStorage.setItem(props.persistKey, JSON.stringify(value))
  } catch {
    // Position persistence is optional; the Pet must remain usable without storage.
  }
  emit('position-change', value)
}

async function resetPosition() {
  try {
    window.localStorage.removeItem(props.persistKey)
  } catch {
    // Storage failure must not block the visible Pet.
  }
  await nextTick()
  applyPosition(defaultPosition())
  emit('position-change', { x: Math.round(position.x), y: Math.round(position.y) })
}

function onPointerDown(event: PointerEvent) {
  if (!props.draggable || event.button !== 0) return
  event.preventDefault()
  dragSession = {
    pointerId: event.pointerId,
    originX: event.clientX,
    originY: event.clientY,
    startX: position.x,
    startY: position.y,
    moved: false,
  }
  isDragging.value = true
  markWindowMoved(false)
  const target = event.currentTarget as HTMLElement | null
  target?.setPointerCapture?.(event.pointerId)
}

function onPointerMove(event: PointerEvent) {
  if (!dragSession || dragSession.pointerId !== event.pointerId) return
  event.preventDefault()
  const deltaX = event.clientX - dragSession.originX
  const deltaY = event.clientY - dragSession.originY
  if (Math.hypot(deltaX, deltaY) >= 4) {
    dragSession.moved = true
    markWindowMoved(true)
  }
  applyPosition({
    x: dragSession.startX + deltaX,
    y: dragSession.startY + deltaY,
  })
}

function endDrag(event: PointerEvent) {
  if (!dragSession || dragSession.pointerId !== event.pointerId) return
  const moved = dragSession.moved
  dragSession = null
  isDragging.value = false
  const target = event.currentTarget as HTMLElement | null
  if (target?.hasPointerCapture?.(event.pointerId)) {
    target.releasePointerCapture(event.pointerId)
  }
  if (moved) savePosition()
  markWindowMoved(moved)
  window.requestAnimationFrame(() => markWindowMoved(false))
}

function onKeyDown(event: KeyboardEvent) {
  if (!props.draggable) return
  const steps: Record<string, PetPosition> = {
    ArrowLeft: { x: -12, y: 0 },
    ArrowRight: { x: 12, y: 0 },
    ArrowUp: { x: 0, y: -12 },
    ArrowDown: { x: 0, y: 12 },
  }
  const step = steps[event.key]
  if (!step) return
  event.preventDefault()
  applyPosition({ x: position.x + step.x, y: position.y + step.y })
  savePosition()
}

function onAssetError() {
  assetAvailable.value = false
  emit('asset-error', frameUrl.value)
}

function animate(timestamp: number) {
  if (!lastFrameAt) lastFrameAt = timestamp
  const actionDefinition = getPetActionDefinition(props.petId, activeAction.value)
  const duration = actionDefinition.durationMs
  if (!prefersReducedMotion.value && timestamp - lastFrameAt >= duration) {
    const elapsedFrames = Math.floor((timestamp - lastFrameAt) / duration)
    frameIndex.value = (frameIndex.value + elapsedFrames) % actionDefinition.frameCount
    lastFrameAt += elapsedFrames * duration
  }
  animationFrameId = window.requestAnimationFrame(animate)
}

function preloadFrames() {
  for (const action of getPetActions(props.petId)) {
    const actionDefinition = getPetActionDefinition(props.petId, action)
    for (let index = 0; index < actionDefinition.frameCount; index += 1) {
      const image = new Image()
      image.src = getPetFrame(props.petId, action, index)
    }
  }
}

function onMotionPreferenceChange(event: MediaQueryListEvent) {
  prefersReducedMotion.value = event.matches
  frameIndex.value = 0
}

function onViewportResize() {
  applyPosition(position)
}

watch(activeAction, () => {
  frameIndex.value = 0
  lastFrameAt = 0
  assetAvailable.value = true
})

watch(() => props.petId, () => {
  assetAvailable.value = true
  preloadFrames()
})

onMounted(async () => {
  motionQuery = window.matchMedia?.('(prefers-reduced-motion: reduce)') ?? null
  prefersReducedMotion.value = motionQuery?.matches ?? false
  motionQuery?.addEventListener?.('change', onMotionPreferenceChange)
  preloadFrames()
  await nextTick()
  applyPosition(readStoredPosition() ?? defaultPosition())
  isReady.value = true
  window.addEventListener('resize', onViewportResize)
  animationFrameId = window.requestAnimationFrame(animate)
})

onBeforeUnmount(() => {
  window.cancelAnimationFrame(animationFrameId)
  window.removeEventListener('resize', onViewportResize)
  motionQuery?.removeEventListener?.('change', onMotionPreferenceChange)
  markWindowMoved(false)
})

defineExpose({ resetPosition })
</script>

<template>
  <button
    v-if="visible && assetAvailable"
    ref="petElement"
    type="button"
    class="desktop-pet"
    :class="{ 'is-dragging': isDragging, 'is-ready': isReady }"
    :style="petStyle"
    :aria-label="petLabel"
    :data-pet-id="petId"
    :data-action="activeAction"
    @pointerdown="onPointerDown"
    @pointermove="onPointerMove"
    @pointerup="endDrag"
    @pointercancel="endDrag"
    @keydown="onKeyDown"
  >
    <img
      :src="frameUrl"
      alt=""
      width="256"
      height="288"
      draggable="false"
      @error="onAssetError"
    >
  </button>
</template>

<style scoped>
.desktop-pet {
  position: fixed;
  z-index: 1200;
  top: 0;
  left: 0;
  width: var(--pet-width);
  aspect-ratio: 256 / 288;
  padding: 0;
  border: 0;
  background: transparent;
  opacity: 0;
  cursor: grab;
  user-select: none;
  touch-action: none;
  will-change: transform;
  filter: drop-shadow(0 12px 16px rgb(0 0 0 / 28%));
  transition: opacity 180ms ease, filter 180ms ease;
}

.desktop-pet.is-ready {
  opacity: 1;
}

.desktop-pet.is-dragging {
  cursor: grabbing;
  filter: drop-shadow(0 18px 24px rgb(0 0 0 / 38%));
}

.desktop-pet img {
  display: block;
  width: 100%;
  height: 100%;
  pointer-events: none;
  object-fit: contain;
}

@media (prefers-reduced-motion: reduce) {
  .desktop-pet {
    transition: none;
  }
}
</style>
