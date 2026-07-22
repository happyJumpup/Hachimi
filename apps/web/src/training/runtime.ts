import { trainingRepository } from '@/db/training-repository'
import { estimateCalories } from '@/features/experience/calorie'
import { createTrainingEngine } from '@/training/training-engine'

export const trainingEngine = createTrainingEngine({
  persistence: trainingRepository,
  estimateCalories: ({ profile, activeMilliseconds, creditedRestMilliseconds }) => {
    const estimate = estimateCalories({
      profile,
      activeMilliseconds,
      creditedRestMilliseconds,
    })
    return { value: estimate.kcal, method: estimate.method }
  },
})
