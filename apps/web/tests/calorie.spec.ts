import { describe, expect, it } from 'vitest'

import { estimateCalories, isCompleteTrainingProfile } from '@/features/experience/calorie'

describe('calorie estimate', () => {
  it('uses the approved personalized formula for a complete profile', () => {
    const profile = {
      sex: 'male' as const,
      age: 30,
      heightCm: 180,
      weightKg: 75,
    }

    expect(isCompleteTrainingProfile(profile)).toBe(true)
    expect(
      estimateCalories({
        profile,
        activeMilliseconds: 20 * 60_000,
        creditedRestMilliseconds: 5 * 60_000,
      }),
    ).toEqual({ kcal: 90, method: 'personalized', label: '约 90 千卡' })
  })

  it('uses the generic rule when any profile field is missing or invalid', () => {
    expect(
      estimateCalories({
        profile: { sex: 'female', age: 17, heightCm: 165, weightKg: 55 },
        activeMilliseconds: 10 * 60_000,
        creditedRestMilliseconds: 2 * 60_000,
      }),
    ).toEqual({ kcal: 42, method: 'generic', label: '约 42 千卡' })
  })

  it('clamps negative or non-finite durations instead of creating impossible calories', () => {
    expect(
      estimateCalories({
        profile: null,
        activeMilliseconds: Number.POSITIVE_INFINITY,
        creditedRestMilliseconds: -100,
      }),
    ).toEqual({ kcal: 0, method: 'generic', label: '约 0 千卡' })
  })
})
