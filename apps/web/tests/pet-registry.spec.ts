import { describe, expect, it } from 'vitest'

import {
  getPetActionDefinition,
  getPetFrame,
  PET_IDS,
  PET_REGISTRY,
  resolvePetAction,
} from '@/domain/pet'

describe('pet registry', () => {
  it('contains exactly seven complete character definitions', () => {
    expect(PET_IDS).toHaveLength(7)
    expect(new Set(PET_IDS).size).toBe(7)

    for (const petId of PET_IDS) {
      const pet = PET_REGISTRY[petId]
      expect(pet.id).toBe(petId)
      expect(pet.actions).toHaveLength(4)
      expect(resolvePetAction(petId, 'idle')).toBeTruthy()
      expect(resolvePetAction(petId, 'drag')).toBeTruthy()

      for (const action of pet.actions) {
        const definition = getPetActionDefinition(petId, action)
        expect(definition.frameCount).toBe(6)
        expect(definition.durationMs).toBeGreaterThan(0)
        expect(getPetFrame(petId, action, 0)).toContain(
          `/pets/${petId}/frames/${action}/${petId}_${action}_01.png`,
        )
      }
    }
  })

  it('falls back to a character idle action when a direct action is unsupported', () => {
    expect(resolvePetAction('gentle', 'idle', 'cheer')).toBe('idle')
    expect(resolvePetAction('challenger', 'countdown')).toBe('countdown')
  })
})
