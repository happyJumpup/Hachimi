<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import type { PetState } from './pet-state'

const props = withDefaults(
  defineProps<{
    state: PetState
    visible?: boolean
  }>(),
  { visible: true },
)

const assetFailed = ref(false)

const coachPresentation = {
  idle: {
    url: new URL('../../assets/pet/idle.webp', import.meta.url).href,
    alt: 'TrainPal 小猫教练在旁边等你开始',
    fallback: 'TrainPal 正在等你，训练不受影响',
  },
  training: {
    url: new URL('../../assets/pet/training.webp', import.meta.url).href,
    alt: 'TrainPal 小猫教练正在陪你训练',
    fallback: 'TrainPal 正在陪你训练，训练不受影响',
  },
  resting: {
    url: new URL('../../assets/pet/resting.webp', import.meta.url).href,
    alt: 'TrainPal 小猫教练正在陪你休息',
    fallback: 'TrainPal 正在陪你休息，训练不受影响',
  },
  paused: {
    url: new URL('../../assets/pet/paused.webp', import.meta.url).href,
    alt: 'TrainPal 小猫教练正在等你继续',
    fallback: 'TrainPal 正在等你继续，训练不受影响',
  },
  completed: {
    url: new URL('../../assets/pet/completed.webp', import.meta.url).href,
    alt: 'TrainPal 小猫教练在庆祝训练完成',
    fallback: 'TrainPal 为你庆祝，训练结果不受影响',
  },
} as const satisfies Record<PetState, { url: string; alt: string; fallback: string }>

const presentation = computed(() => coachPresentation[props.state])

watch(
  () => props.state,
  () => {
    assetFailed.value = false
  },
)
</script>

<template>
  <figure v-if="visible" class="trainpal-coach" :data-state="state">
    <img
      v-if="!assetFailed"
      class="trainpal-coach__image"
      :src="presentation.url"
      :alt="presentation.alt"
      width="112"
      height="112"
      @error="assetFailed = true"
    />
    <figcaption v-else class="trainpal-coach__fallback">
      {{ presentation.fallback }}
    </figcaption>
  </figure>
</template>

<style scoped>
.trainpal-coach {
  display: grid;
  width: fit-content;
  margin: 0;
  place-items: center;
}

.trainpal-coach__image {
  display: block;
  width: clamp(88px, 28vw, 112px);
  height: auto;
  object-fit: contain;
  filter: drop-shadow(0 10px 24px rgb(0 0 0 / 42%));
  transform-origin: 50% 84%;
}

[data-state='idle'] .trainpal-coach__image {
  animation: coach-breathe 3.2s ease-in-out infinite;
}

[data-state='training'] .trainpal-coach__image {
  animation: coach-train 0.82s ease-in-out infinite;
}

[data-state='resting'] .trainpal-coach__image {
  animation: coach-rest 2.6s ease-in-out infinite;
}

[data-state='completed'] .trainpal-coach__image {
  animation: coach-celebrate 720ms cubic-bezier(0.2, 0.8, 0.2, 1) 1;
}

.trainpal-coach__fallback {
  max-width: 168px;
  color: #9eaaa7;
  font-size: 12px;
  line-height: 1.45;
  text-align: center;
}

@keyframes coach-breathe {
  0%,
  100% {
    transform: translateY(0) scale(1);
  }
  50% {
    transform: translateY(-3px) scale(1.015);
  }
}

@keyframes coach-train {
  0%,
  100% {
    transform: translateY(0) rotate(-1deg);
  }
  50% {
    transform: translateY(-5px) rotate(1deg);
  }
}

@keyframes coach-rest {
  0%,
  100% {
    transform: scale(0.985);
    opacity: 0.88;
  }
  50% {
    transform: scale(1);
    opacity: 1;
  }
}

@keyframes coach-celebrate {
  0% {
    transform: translateY(10px) scale(0.9);
  }
  55% {
    transform: translateY(-12px) scale(1.06);
  }
  100% {
    transform: translateY(0) scale(1);
  }
}

@media (prefers-reduced-motion: reduce) {
  .trainpal-coach__image {
    animation: none !important;
  }
}
</style>
