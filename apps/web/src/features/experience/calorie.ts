export type ProfileSex = 'male' | 'female'

export interface TrainingProfileInput {
  sex?: ProfileSex | null
  age?: number | null
  heightCm?: number | null
  weightKg?: number | null
}

export interface CompleteTrainingProfile {
  sex: ProfileSex
  age: number
  heightCm: number
  weightKg: number
}

export interface CalorieEstimate {
  kcal: number
  method: 'personalized' | 'generic'
  label: string
}

interface CalorieEstimateInput {
  profile: TrainingProfileInput | null
  activeMilliseconds: number
  creditedRestMilliseconds: number
}

const MINIMUM_AGE = 18
const MAXIMUM_AGE = 100
const MINIMUM_HEIGHT_CM = 100
const MAXIMUM_HEIGHT_CM = 250
const MINIMUM_WEIGHT_KG = 20
const MAXIMUM_WEIGHT_KG = 300
const MINUTES_PER_DAY = 1_440

function isFiniteInRange(value: number | null | undefined, minimum: number, maximum: number): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= minimum && value <= maximum
}

export function isCompleteTrainingProfile(
  profile: TrainingProfileInput | null,
): profile is CompleteTrainingProfile {
  if (profile === null) return false

  return (
    (profile.sex === 'male' || profile.sex === 'female') &&
    isFiniteInRange(profile.age, MINIMUM_AGE, MAXIMUM_AGE) &&
    Number.isInteger(profile.age) &&
    isFiniteInRange(profile.heightCm, MINIMUM_HEIGHT_CM, MAXIMUM_HEIGHT_CM) &&
    isFiniteInRange(profile.weightKg, MINIMUM_WEIGHT_KG, MAXIMUM_WEIGHT_KG)
  )
}

function durationMinutes(milliseconds: number): number {
  if (!Number.isFinite(milliseconds) || milliseconds <= 0) return 0
  return milliseconds / 60_000
}

export function estimateCalories(input: CalorieEstimateInput): CalorieEstimate {
  const activeMinutes = durationMinutes(input.activeMilliseconds)
  const creditedRestMinutes = durationMinutes(input.creditedRestMilliseconds)

  let method: CalorieEstimate['method'] = 'generic'
  let unroundedKcal = 4 * activeMinutes + creditedRestMinutes

  if (isCompleteTrainingProfile(input.profile)) {
    const sexOffset = input.profile.sex === 'male' ? 5 : -161
    const restingMetabolicRate =
      10 * input.profile.weightKg +
      6.25 * input.profile.heightCm -
      5 * input.profile.age +
      sexOffset

    method = 'personalized'
    unroundedKcal =
      (restingMetabolicRate / MINUTES_PER_DAY) *
      (3.5 * activeMinutes + creditedRestMinutes)
  }

  const kcal = Math.max(0, Math.round(Number.isFinite(unroundedKcal) ? unroundedKcal : 0))
  return { kcal, method, label: `约 ${kcal} 千卡` }
}
