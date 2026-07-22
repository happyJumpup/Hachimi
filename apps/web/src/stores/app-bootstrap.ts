import { ref } from 'vue'
import { defineStore } from 'pinia'

type BootstrapStatus = 'loading' | 'ready' | 'failed'
type BootstrapOperation = () => Promise<void>

export const useAppBootstrapStore = defineStore('app-bootstrap', () => {
  const status = ref<BootstrapStatus>('loading')
  let operation: BootstrapOperation | null = null
  let running: Promise<void> | null = null

  function initialize(nextOperation?: BootstrapOperation): Promise<void> {
    if (nextOperation) operation = nextOperation
    if (running) return running

    status.value = 'loading'
    running = (async () => {
      try {
        if (!operation) throw new Error('bootstrap operation is unavailable')
        await operation()
        status.value = 'ready'
      } catch {
        status.value = 'failed'
      } finally {
        running = null
      }
    })()
    return running
  }

  const retry = (): Promise<void> => initialize()

  return { status, initialize, retry }
})
