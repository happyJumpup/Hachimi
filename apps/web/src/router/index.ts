import { createRouter, createWebHistory } from 'vue-router'

import PlanDraftView from '@/views/PlanDraftView.vue'
import TrainingView from '@/views/TrainingView.vue'
import VideoAnalysisView from '@/views/VideoAnalysisView.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'video', component: VideoAnalysisView },
    { path: '/plan', name: 'plan', component: PlanDraftView },
    { path: '/training', name: 'training', component: TrainingView },
  ],
  scrollBehavior: () => ({ top: 0 }),
})
