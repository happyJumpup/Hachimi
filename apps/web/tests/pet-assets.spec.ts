import { existsSync, statSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

import { PET_IDS, PET_REGISTRY } from '@/domain/pet'

const publicPetsRoot = resolve(process.cwd(), 'public', 'pets')

describe.each(PET_IDS)('%s runtime assets', (petId) => {
  it('contains every registered frame as a non-empty PNG', () => {
    const definition = PET_REGISTRY[petId]

    for (const action of definition.actions) {
      const actionDefinition = definition.actionDefinitions[action]
      expect(actionDefinition).toBeDefined()

      for (let frame = 1; frame <= actionDefinition!.frameCount; frame += 1) {
        const filename = `${petId}_${action}_${String(frame).padStart(2, '0')}.png`
        const path = resolve(publicPetsRoot, petId, 'frames', action, filename)
        expect(existsSync(path), `Missing ${path}`).toBe(true)
        expect(statSync(path).size, `Empty ${path}`).toBeGreaterThan(0)
      }
    }
  })
})
