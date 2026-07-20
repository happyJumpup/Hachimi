import { ref } from 'vue'
import { defineStore } from 'pinia'

import type { LibraryRepository } from '@/db/library-repository'
import type { DraftPlan } from '@/domain/types'
import type {
  Preferences,
  SavedPlan,
  TrainingProfile,
  TrainingRecord,
} from '@/domain/training'
import {
  QUICK_EXPERIENCE_PLAN_NAME,
  createQuickExperienceDraftItems,
} from '@/features/quick-experience/fixture'

const emptyProfile = (): TrainingProfile => ({
  id: 'current',
  sex: null,
  age: null,
  heightCm: null,
  weightKg: null,
  updatedAt: new Date(0).toISOString(),
})

const defaultPreferences = (): Preferences => ({
  id: 'current',
  petVisible: true,
  updatedAt: new Date(0).toISOString(),
})

export const useLibraryStore = defineStore('library', () => {
  const plans = ref<SavedPlan[]>([])
  const records = ref<TrainingRecord[]>([])
  const profile = ref<TrainingProfile>(emptyProfile())
  const preferences = ref<Preferences>(defaultPreferences())
  const loaded = ref(false)
  let repository: LibraryRepository | null = null

  const requireRepository = (): LibraryRepository => {
    if (!repository) throw new Error('local training library is not loaded')
    return repository
  }

  async function load(nextRepository: LibraryRepository): Promise<void> {
    repository = nextRepository
    const [savedPlans, savedRecords, savedProfile, savedPreferences] = await Promise.all([
      repository.listPlans(),
      repository.listRecords(),
      repository.loadProfile(),
      repository.loadPreferences(),
    ])
    plans.value = savedPlans
    records.value = savedRecords
    profile.value = savedProfile ?? emptyProfile()
    preferences.value = savedPreferences ?? defaultPreferences()
    loaded.value = true
  }

  async function refreshHistory(): Promise<void> {
    const currentRepository = requireRepository()
    ;[plans.value, records.value] = await Promise.all([
      currentRepository.listPlans(),
      currentRepository.listRecords(),
    ])
  }

  async function saveCurrentDraftAs(name: string): Promise<DraftPlan> {
    const result = await requireRepository().saveCurrentDraftAs(name)
    plans.value = [result.plan, ...plans.value.filter((plan) => plan.id !== result.plan.id)]
    return result.draft
  }

  async function openPlan(planId: string): Promise<DraftPlan> {
    return requireRepository().openPlan(planId)
  }

  async function useQuickExperience(): Promise<DraftPlan> {
    return requireRepository().replaceCurrentDraft({
      name: QUICK_EXPERIENCE_PLAN_NAME,
      items: createQuickExperienceDraftItems(),
    })
  }

  async function saveProfile(
    input: Omit<TrainingProfile, 'id' | 'updatedAt'>,
  ): Promise<void> {
    profile.value = await requireRepository().saveProfile(input)
  }

  async function clearProfile(): Promise<void> {
    await saveProfile({ sex: null, age: null, heightCm: null, weightKg: null })
  }

  async function setPetVisible(petVisible: boolean): Promise<void> {
    preferences.value = await requireRepository().savePreferences({ petVisible })
  }

  async function clearAllLocalData(): Promise<void> {
    await requireRepository().clearAllLocalData()
    plans.value = []
    records.value = []
    profile.value = emptyProfile()
    preferences.value = defaultPreferences()
  }

  return {
    plans,
    records,
    profile,
    preferences,
    loaded,
    load,
    refreshHistory,
    saveCurrentDraftAs,
    openPlan,
    useQuickExperience,
    saveProfile,
    clearProfile,
    setPetVisible,
    clearAllLocalData,
  }
})
