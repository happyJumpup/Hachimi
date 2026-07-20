import '@fontsource/barlow-condensed/latin-500.css'
import '@fontsource/barlow-condensed/latin-600.css'
import '@fontsource/barlow-condensed/latin-700.css'
import '@/styles/base.css'

import { createApp } from 'vue'
import { createPinia } from 'pinia'

import App from '@/App.vue'
import { accessClient } from '@/api/client'
import { draftRepository } from '@/db/draft-repository'
import { libraryRepository } from '@/db/library-repository'
import { localDataClearCoordinator } from '@/local-data/clear-coordinator'
import { router } from '@/router'
import { useDraftStore } from '@/stores/draft'
import { useAccessStore } from '@/stores/access'
import { useAppBootstrapStore } from '@/stores/app-bootstrap'
import { useLibraryStore } from '@/stores/library'
import { useLocalDataClearStore } from '@/stores/local-data-clear'
import { useTrainingStore } from '@/stores/training'
import { trainingEngine } from '@/training/runtime'

const app = createApp(App)
const pinia = createPinia()

app.use(pinia)
app.use(router)

app.mount('#app')

void useAppBootstrapStore(pinia).initialize(async () => {
  await Promise.all([
    useAccessStore(pinia).load(accessClient),
    useDraftStore(pinia).load(draftRepository),
    useLibraryStore(pinia).load(libraryRepository),
    useTrainingStore(pinia).load(trainingEngine),
  ])
  useLocalDataClearStore(pinia).initialize(localDataClearCoordinator)
})
