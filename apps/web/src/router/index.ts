import { createRouter, createWebHistory } from 'vue-router'

import PlanDraftView from '@/views/PlanDraftView.vue'
import PetPreviewView from '@/views/PetPreviewView.vue'
import VideoAnalysisView from '@/views/VideoAnalysisView.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'video', component: VideoAnalysisView },
    { path: '/plan', name: 'plan', component: PlanDraftView },
    { path: '/pet-preview', name: 'pet-preview', component: PetPreviewView },
  ],
  scrollBehavior: () => ({ top: 0 }),
})
