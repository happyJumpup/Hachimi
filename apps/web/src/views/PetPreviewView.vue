<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import DesktopPet from '@/components/DesktopPet.vue'
import {
  getPetActions,
  getPetDefinition,
  PET_ACTION_DESCRIPTIONS,
  PET_ACTION_LABELS,
  PET_IDS,
  type PetAction,
  type PetId,
} from '@/domain/pet'

interface DesktopPetHandle {
  resetPosition: () => Promise<void>
}

const SELECTED_PET_KEY = 'hachimi.desktop-pet.selected.v1'

function readSelectedPet(): PetId {
  try {
    const stored = window.localStorage.getItem(SELECTED_PET_KEY) as PetId | null
    if (stored && PET_IDS.includes(stored)) return stored
  } catch {
    // The preview remains usable when storage is unavailable.
  }
  return 'hotblood'
}

const selectedPet = ref<PetId>(readSelectedPet())
const selectedAction = ref<PetAction>(getPetActions(selectedPet.value)[0]!)
const pet = ref<DesktopPetHandle | null>(null)

const selectedDefinition = computed(() => getPetDefinition(selectedPet.value))
const availableActions = computed(() => getPetActions(selectedPet.value))
const selectedCopy = computed(() => PET_ACTION_DESCRIPTIONS[selectedAction.value])
const dragAction = computed(() => selectedDefinition.value.cueMap.drag)

watch(selectedPet, (petId) => {
  selectedAction.value = getPetActions(petId)[0]!
  try {
    window.localStorage.setItem(SELECTED_PET_KEY, petId)
  } catch {
    // Selection persistence is optional in the review page.
  }
})

async function resetPetPosition() {
  await pet.value?.resetPosition()
}
</script>

<template>
  <main class="pet-preview-page">
    <header class="preview-header">
      <RouterLink class="back-link" to="/plan">返回方案草稿</RouterLink>
      <p class="eyebrow">HACHIMI PET LAB</p>
      <h1>七猫桌宠动画验收台</h1>
      <p class="lede">逐只检查角色形象与动作循环，也可以直接拖动右下角的{{ selectedDefinition.shortName }}。松手后刷新页面，角色与位置都会保留。</p>
    </header>

    <section class="control-card" aria-labelledby="pet-character-heading">
      <div class="card-heading">
        <div>
          <p class="section-index">01 / CHARACTER</p>
          <h2 id="pet-character-heading">选择角色</h2>
        </div>
        <span class="active-pill">{{ selectedDefinition.shortName }}</span>
      </div>

      <div class="character-grid" role="group" aria-label="选择桌宠角色">
        <button
          v-for="petId in PET_IDS"
          :key="petId"
          type="button"
          class="character-button"
          :class="{ active: selectedPet === petId }"
          :aria-pressed="selectedPet === petId"
          @click="selectedPet = petId"
        >
          <span>{{ getPetDefinition(petId).displayName }}</span>
          <small>{{ petId }}</small>
        </button>
      </div>

      <p class="action-description">{{ selectedDefinition.personality }}</p>
    </section>

    <section class="control-card action-card" aria-labelledby="pet-action-heading">
      <div class="card-heading">
        <div>
          <p class="section-index">02 / ACTION</p>
          <h2 id="pet-action-heading">动作状态</h2>
        </div>
        <span class="active-pill">{{ PET_ACTION_LABELS[selectedAction] }}</span>
      </div>

      <div class="action-grid" role="group" :aria-label="`选择${selectedDefinition.shortName}动作`">
        <button
          v-for="action in availableActions"
          :key="action"
          type="button"
          class="action-button"
          :class="{ active: selectedAction === action }"
          :aria-pressed="selectedAction === action"
          @click="selectedAction = action"
        >
          <span>{{ PET_ACTION_LABELS[action] }}</span>
          <small>{{ action }}</small>
        </button>
      </div>

      <p class="action-description">{{ selectedCopy }}</p>
    </section>

    <section class="instruction-card" aria-labelledby="pet-interaction-heading">
      <div>
        <p class="section-index">03 / INTERACTION</p>
        <h2 id="pet-interaction-heading">拖动与记忆</h2>
      </div>
      <ol>
        <li>按住{{ selectedDefinition.shortName }}并拖到屏幕任意位置。</li>
        <li>拖动时自动切换为 <strong>{{ dragAction }}</strong> 动作。</li>
        <li>松手后位置保存到当前设备。</li>
      </ol>
      <button type="button" class="reset-button" @click="resetPetPosition">重置到右下角</button>
    </section>

    <p class="non-blocking-note">桌宠是独立呈现层；图片加载失败时页面其他功能仍然可用。</p>

    <DesktopPet ref="pet" :pet-id="selectedPet" :action="selectedAction" />
  </main>
</template>

<style scoped>
.pet-preview-page {
  position: relative;
  min-height: 100dvh;
  overflow: hidden;
  padding: 28px 20px 220px;
  background:
    linear-gradient(135deg, rgb(255 111 97 / 8%), transparent 45%),
    repeating-linear-gradient(90deg, transparent 0 31px, rgb(255 255 255 / 2.5%) 32px),
    var(--bg);
}

.pet-preview-page::before {
  position: absolute;
  top: 22px;
  right: -78px;
  width: 190px;
  height: 190px;
  border: 28px solid rgb(38 235 213 / 7%);
  border-radius: 50%;
  content: '';
}

.preview-header,
.control-card,
.instruction-card,
.non-blocking-note {
  position: relative;
  z-index: 1;
  max-width: 720px;
  margin-right: auto;
  margin-left: auto;
}

.back-link {
  display: inline-flex;
  margin-bottom: 40px;
  color: var(--muted);
  font-size: .86rem;
  text-decoration: none;
}

.back-link::before {
  margin-right: 8px;
  content: '←';
}

.eyebrow,
.section-index {
  margin: 0;
  color: var(--cyan);
  font-family: var(--font-display);
  font-size: .78rem;
  font-weight: 700;
  letter-spacing: .16em;
}

h1,
h2 {
  margin: 0;
  font-family: var(--font-display), var(--font-cn);
  font-weight: 700;
  line-height: .96;
}

h1 {
  max-width: 420px;
  margin-top: 10px;
  font-size: clamp(3rem, 14vw, 5.6rem);
  letter-spacing: -.035em;
}

h2 {
  margin-top: 5px;
  font-size: 1.7rem;
}

.lede {
  max-width: 560px;
  margin: 22px 0 34px;
  color: #b7c0c4;
  font-size: .96rem;
  line-height: 1.75;
}

.control-card,
.instruction-card {
  padding: 22px;
  border: 1px solid var(--line-strong);
  border-radius: 22px;
  background: rgb(16 20 23 / 88%);
  box-shadow: 0 24px 70px rgb(0 0 0 / 24%);
  backdrop-filter: blur(12px);
}

.action-card {
  margin-top: 14px;
}

.card-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.active-pill {
  padding: 7px 12px;
  border: 1px solid rgb(38 235 213 / 28%);
  border-radius: 99px;
  color: var(--cyan);
  background: rgb(38 235 213 / 8%);
  font-size: .78rem;
}

.action-grid,
.character-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-top: 22px;
}

.action-button,
.character-button {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  min-height: 54px;
  padding: 12px 14px;
  border: 1px solid var(--line);
  border-radius: 14px;
  color: var(--ink);
  background: var(--surface-raised);
  text-align: left;
  transition: border-color 160ms ease, background 160ms ease, transform 160ms ease;
}

.action-button:hover,
.character-button:hover {
  transform: translateY(-1px);
  border-color: var(--line-strong);
}

.action-button.active,
.character-button.active {
  border-color: var(--coral);
  background: rgb(255 111 97 / 12%);
}

.action-button span,
.character-button span {
  font-weight: 700;
}

.action-button small,
.character-button small {
  color: var(--muted);
  font-family: var(--font-display);
  letter-spacing: .06em;
}

.character-button {
  min-height: 64px;
}

.action-description {
  min-height: 48px;
  margin: 18px 0 0;
  color: #aab3b8;
  font-size: .88rem;
  line-height: 1.65;
}

.instruction-card {
  display: grid;
  gap: 18px;
  margin-top: 14px;
}

.instruction-card ol {
  display: grid;
  gap: 8px;
  margin: 0;
  padding-left: 22px;
  color: #aab3b8;
  font-size: .86rem;
  line-height: 1.55;
}

.instruction-card strong {
  color: var(--coral);
  font-family: var(--font-display);
  letter-spacing: .04em;
}

.reset-button {
  justify-self: start;
  padding: 10px 15px;
  border: 1px solid var(--line-strong);
  border-radius: 12px;
  color: var(--ink);
  background: transparent;
}

.non-blocking-note {
  margin-top: 20px;
  color: #667177;
  font-size: .75rem;
  line-height: 1.6;
}

@media (min-width: 700px) {
  .pet-preview-page {
    padding-top: 42px;
  }

  .instruction-card {
    grid-template-columns: 1fr 1.4fr;
    align-items: center;
  }

  .instruction-card .reset-button {
    grid-column: 2;
  }
}

@media (prefers-reduced-motion: reduce) {
  .action-button {
    transition: none;
  }
}
</style>
