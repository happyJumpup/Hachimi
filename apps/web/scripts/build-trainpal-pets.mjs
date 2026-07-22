import { access, mkdir, rm } from 'node:fs/promises'
import { resolve } from 'node:path'
import process from 'node:process'

import sharp from 'sharp'

const actionsByStyle = {
  hotblood: ['idle', 'cheer', 'hop', 'drag'],
  gentle: ['idle', 'comfort', 'guide', 'rest'],
  snarky: ['idle', 'mock', 'point', 'annoyed'],
  analyst: ['idle', 'scan', 'analyze', 'report'],
  comedian: ['idle', 'talk', 'laugh', 'celebrate'],
  challenger: ['idle', 'challenge', 'countdown', 'power-up'],
  zen: ['idle', 'breathe', 'nod', 'relax'],
}

const sourceIndex = process.argv.indexOf('--source')
const sourceValue = sourceIndex >= 0 ? process.argv[sourceIndex + 1] : null
if (!sourceValue) {
  throw new Error('usage: pnpm pets:build -- --source <raw pets directory>')
}

const sourceRoot = resolve(sourceValue)
const outputRoot = resolve(import.meta.dirname, '../public/trainpal/pets')
const expectedOutputSuffix = resolve('public/trainpal/pets')
if (!outputRoot.endsWith(expectedOutputSuffix)) {
  throw new Error(`refusing to replace unexpected output directory: ${outputRoot}`)
}

const jobs = Object.entries(actionsByStyle).flatMap(([styleId, actions]) =>
  actions.flatMap((action) =>
    Array.from({ length: 6 }, (_, index) => {
      const frame = String(index + 1).padStart(2, '0')
      return {
        input: resolve(sourceRoot, styleId, 'frames', action, `${styleId}_${action}_${frame}.png`),
        output: resolve(outputRoot, styleId, action, `${frame}.webp`),
      }
    }),
  ),
)

await Promise.all(jobs.map(({ input }) => access(input)))
await rm(outputRoot, { recursive: true, force: true })

for (const { input, output } of jobs) {
  await mkdir(resolve(output, '..'), { recursive: true })
  await sharp(input)
    .webp({ quality: 88, alphaQuality: 100, smartSubsample: true })
    .toFile(output)
}

process.stdout.write(`wrote ${jobs.length} TrainPal WebP frames to ${outputRoot}\n`)
