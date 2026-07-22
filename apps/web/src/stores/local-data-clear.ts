import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { analysisClient, browserEventStreamFactory } from '@/api/client'
import type { LocalDataClearCoordinator } from '@/local-data/clear-coordinator'
import { useAnalysisStore } from '@/stores/analysis'
import { useDraftStore } from '@/stores/draft'
import { useLibraryStore } from '@/stores/library'
import { useGymtiStore } from '@/stores/gymti'
import { useLocalMediaStore } from '@/stores/local-media'
import { useTrainingStore } from '@/stores/training'

export const useLocalDataClearStore = defineStore('local-data-clear', () => {
  const draft = useDraftStore()
  const library = useLibraryStore()
  const gymti = useGymtiStore()
  const analysis = useAnalysisStore()
  const localMedia = useLocalMediaStore()
  const training = useTrainingStore()
  const initialized = ref(false)
  let coordinator: LocalDataClearCoordinator | null = null

  const supported = computed(() => initialized.value && coordinator?.supported === true)

  function initialize(nextCoordinator: LocalDataClearCoordinator): void {
    coordinator = nextCoordinator
    coordinator.connect({
      prepare: async () => {
        await Promise.all([
          draft.quiescePersistence(),
          library.quiescePersistence(),
          gymti.quiescePersistence(),
          training.quiescePersistence(),
        ])
        analysis.disconnect()
        localMedia.resetLocalState()
        draft.resetLocalState(true)
        library.resetLocalState(true)
        gymti.resetLocalState(true)
        training.resetLocalState(true)
      },
      commit: () => {
        analysis.clearResult()
        draft.resumePersistence()
        library.resumePersistence()
        gymti.resumePersistence()
        training.resumePersistence()
      },
      abort: async () => {
        draft.resumePersistence()
        library.resumePersistence()
        gymti.resumePersistence()
        training.resumePersistence()
        await Promise.allSettled([
          draft.reload(),
          library.reload(),
          gymti.reload(),
          localMedia.restore(),
          analysis.restore({ client: analysisClient, events: browserEventStreamFactory }),
          training.restore(),
        ])
      },
    })
    initialized.value = true
  }

  async function clearAllLocalData(): Promise<void> {
    if (!coordinator) throw new Error('local data clear coordinator is not initialized')
    await coordinator.clear(() => library.clearAllLocalData())
  }

  return {
    initialized,
    supported,
    initialize,
    clearAllLocalData,
  }
})
