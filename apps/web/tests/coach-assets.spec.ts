import { createHash } from 'node:crypto'
import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'
import sharp from 'sharp'

import {
  COACH_ACTIONS,
  COACH_STYLE_IDS,
  coachFrameUrls,
  resolveCoachBaseAction,
  resolveCoachEventAction,
  type CoachAction,
} from '@/domain/coach'

const publicRoot = resolve(import.meta.dirname, '../public')

describe('seven-cat coach assets', () => {
  it('publishes seven stable styles with four six-frame WebP actions each', () => {
    expect(COACH_STYLE_IDS).toEqual([
      'hotblood',
      'gentle',
      'snarky',
      'analyst',
      'comedian',
      'challenger',
      'zen',
    ])

    for (const styleId of COACH_STYLE_IDS) {
      const actions = COACH_ACTIONS[styleId]
      expect(actions).toHaveLength(4)

      for (const action of actions) {
        const urls = coachFrameUrls(styleId, action)
        expect(urls).toHaveLength(6)
        expect(urls.every((url) => url.endsWith('.webp'))).toBe(true)
        expect(urls.every((url) => existsSync(resolve(publicRoot, url.slice(1))))).toBe(true)
      }
    }
  })

  it('keeps original PNG and unused full-body art out of runtime assets', () => {
    const runtimeFiles = readdirSync(resolve(publicRoot, 'trainpal/pets'), {
      recursive: true,
      withFileTypes: true,
    }).filter((entry) => entry.isFile())

    expect(runtimeFiles).toHaveLength(168)
    expect(runtimeFiles.every((entry) => entry.name.endsWith('.webp'))).toBe(true)
    expect(runtimeFiles.some((entry) => entry.name.includes('fullbody'))).toBe(false)
  })

  it('preserves the transparent 256 by 288 frame contract without duplicate action frames', async () => {
    for (const styleId of COACH_STYLE_IDS) {
      for (const action of COACH_ACTIONS[styleId]) {
        const paths = coachFrameUrls(styleId, action)
          .map((url) => resolve(publicRoot, url.slice(1)))
        const metadata = await Promise.all(paths.map((path) => sharp(path).metadata()))
        expect(metadata.every((frame) => (
          frame.width === 256
          && frame.height === 288
          && frame.hasAlpha === true
          && frame.format === 'webp'
        ))).toBe(true)

        const hashes = paths.map((path) =>
          createHash('sha256').update(readFileSync(path)).digest('hex'),
        )
        expect(new Set(hashes)).toHaveLength(6)
      }
    }
  })

  it('uses only the approved automatic motion policy', () => {
    expect(resolveCoachBaseAction('gentle', 'resting')).toBe('rest')
    expect(resolveCoachBaseAction('zen', 'resting')).toBe('breathe')
    expect(resolveCoachBaseAction('hotblood', 'training')).toBe('idle')

    expect(resolveCoachEventAction('analyst', 'set_started')).toBeNull()
    expect(resolveCoachEventAction('analyst', 'set_completed')).toBeNull()
    expect(resolveCoachEventAction('challenger', 'rest_final_countdown')).toBe('countdown')
    expect(resolveCoachEventAction('hotblood', 'session_completed')).toBe('hop')

    const automaticActions = COACH_STYLE_IDS.flatMap((styleId) => [
      resolveCoachEventAction(styleId, 'set_started'),
      resolveCoachEventAction(styleId, 'set_completed'),
      resolveCoachEventAction(styleId, 'rest_final_countdown'),
      resolveCoachEventAction(styleId, 'session_completed'),
    ]).filter((action): action is CoachAction => action !== null)

    expect(automaticActions).not.toEqual(expect.arrayContaining([
      'scan',
      'analyze',
      'mock',
      'annoyed',
      'laugh',
      'drag',
    ]))
  })
})
