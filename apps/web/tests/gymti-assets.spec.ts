import { readFileSync, readdirSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'
import sharp from 'sharp'

import {
  COACH_STYLE_IDS,
  COACH_STYLE_LABELS,
} from '@/domain/coach'
import { GYMTI_TYPES } from '@/domain/gymti'
import {
  COACH_STYLE_PRESENTATION,
  GYMTI_TYPE_PRESENTATION,
  gymtiReasonText,
} from '@/features/gymti/presentation'

const publicRoot = resolve(import.meta.dirname, '../public')
const illustrationRoot = resolve(publicRoot, 'gymti/types')

describe('GYMTI result presentation assets', () => {
  it('publishes one mobile-sized WebP illustration for every stable GYMTI type', async () => {
    expect(Object.keys(GYMTI_TYPE_PRESENTATION)).toEqual(GYMTI_TYPES)

    for (const type of GYMTI_TYPES) {
      const presentation = GYMTI_TYPE_PRESENTATION[type]
      expect(presentation.label.length).toBeGreaterThan(0)
      expect(presentation.shortDescription.length).toBeGreaterThan(0)
      expect(presentation.illustrationUrl).toBe(`/gymti/types/${type.toLowerCase()}.webp`)

      const metadata = await sharp(resolve(publicRoot, presentation.illustrationUrl.slice(1)))
        .metadata()
      expect(metadata.format).toBe('webp')
      expect(metadata.width).toBe(720)
      expect(metadata.height).toBe(588)
    }
  })

  it('keeps source PNGs, preview sheets, and duplicate cat art out of the runtime directory', () => {
    const runtimeFiles = readdirSync(resolve(publicRoot, 'gymti'), {
      recursive: true,
      withFileTypes: true,
    }).filter((entry) => entry.isFile())

    expect(runtimeFiles).toHaveLength(7)
    expect(runtimeFiles.every((entry) => entry.name.endsWith('.webp'))).toBe(true)
    expect(runtimeFiles.some((entry) => /png|preview|cat|pet|fullbody/i.test(entry.name))).toBe(false)
    expect(readdirSync(illustrationRoot).sort()).toEqual(
      GYMTI_TYPES.map((type) => `${type.toLowerCase()}.webp`).sort(),
    )
  })

  it('covers all seven shared coach style ids without duplicating their labels or assets', () => {
    expect(Object.keys(COACH_STYLE_PRESENTATION)).toEqual(COACH_STYLE_IDS)

    for (const styleId of COACH_STYLE_IDS) {
      const presentation = COACH_STYLE_PRESENTATION[styleId]
      expect(presentation.label).toBe(COACH_STYLE_LABELS[styleId])
      expect(presentation.personality.length).toBeGreaterThan(0)
      expect(presentation.matchReason.length).toBeGreaterThan(0)
      expect(Object.keys(presentation)).not.toContain('assetUrl')
    }
  })

  it('uses a safe generic display sentence for unknown or exclusion-only reason codes', () => {
    const contract = JSON.parse(readFileSync(
      resolve(import.meta.dirname, '../../../contracts/gymti-questionnaire.v1.json'),
      'utf8',
    )) as { reasonCodes: Array<{ id: string }> }
    const fallback = gymtiReasonText('unknown_reason')

    for (const reason of contract.reasonCodes) {
      expect(gymtiReasonText(reason.id)).not.toBe(fallback)
    }

    expect(gymtiReasonText('hard_exclude_snarky')).toBe(gymtiReasonText('unknown_reason'))
    expect(gymtiReasonText('hard_exclude_snarky')).not.toMatch(/排除|伤害|静音|攻击/)
  })
})
