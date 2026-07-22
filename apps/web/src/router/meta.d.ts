import 'vue-router'

export {}

declare module 'vue-router' {
  interface RouteMeta {
    shell?: 'top-level' | 'immersive'
    theme?: 'journal' | 'training'
    showBottomNav?: boolean
    showAnalysisTask?: boolean
    showTrainingTask?: boolean
    taskDockAboveAction?: boolean
    title?: string
  }
}
