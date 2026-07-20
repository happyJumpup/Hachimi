import { trainingRepository } from '@/db/training-repository'
import { createTrainingEngine } from '@/training/training-engine'

export const trainingEngine = createTrainingEngine({
  persistence: trainingRepository,
})
