import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import type { LocalDataClearCoordinator } from '@/local-data/clear-coordinator'
import { useDraftStore } from '@/stores/draft'
import { useLibraryStore } from '@/stores/library'
import { useTrainingStore } from '@/stores/training'

export const useLocalDataClearStore = defineStore('local-data-clear', () => {
  const draft = useDraftStore()
  const library = useLibraryStore()
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
          training.quiescePersistence(),
        ])
        draft.resetLocalState(true)
        library.resetLocalState(true)
        training.resetLocalState(true)
      },
      commit: () => {
        draft.resumePersistence()
        library.resumePersistence()
        training.resumePersistence()
      },
      abort: async () => {
        draft.resumePersistence()
        library.resumePersistence()
        training.resumePersistence()
        await Promise.allSettled([
          draft.reload(),
          library.reload(),
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
