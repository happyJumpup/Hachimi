import { createRouter, createWebHistory } from 'vue-router'

import MineView from '@/views/MineView.vue'
import PlanDraftView from '@/views/PlanDraftView.vue'
import ResultView from '@/views/ResultView.vue'
import TrainingView from '@/views/TrainingView.vue'
import VideoAnalysisView from '@/views/VideoAnalysisView.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'video', component: VideoAnalysisView },
    { path: '/plan', name: 'plan', component: PlanDraftView },
    { path: '/mine', name: 'mine', component: MineView },
    { path: '/training', name: 'training', component: TrainingView },
    { path: '/result/:recordId', name: 'result', component: ResultView },
  ],
  scrollBehavior: () => ({ top: 0 }),
})
