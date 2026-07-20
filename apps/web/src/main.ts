import '@fontsource/barlow-condensed/latin-500.css'
import '@fontsource/barlow-condensed/latin-600.css'
import '@fontsource/barlow-condensed/latin-700.css'
import '@/styles/base.css'

import { createApp } from 'vue'
import { createPinia } from 'pinia'

import App from '@/App.vue'
import { draftRepository } from '@/db/draft-repository'
import { router } from '@/router'
import { useDraftStore } from '@/stores/draft'

const app = createApp(App)
const pinia = createPinia()

app.use(pinia)
app.use(router)

await useDraftStore(pinia).load(draftRepository)
app.mount('#app')
