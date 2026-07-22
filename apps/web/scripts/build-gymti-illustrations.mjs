import { mkdir } from 'node:fs/promises'
import { resolve } from 'node:path'
import { argv, stdout } from 'node:process'
import { fileURLToPath, URL } from 'node:url'

import sharp from 'sharp'

const sourceFiles = {
  imnb: 'GYMTI-IMNB.png',
  kcal: 'GYMTI-KCAL.png',
  hide: 'GYMTI-HIDE.png',
  life: 'GYMTI-LIFE.png',
  curv: 'GYMTI-CURV.png',
  boom: 'GYMTI-BOOM.png',
  winn: 'GYMTI-WINNer.png',
}

const [inputDirectoryArgument, outputDirectoryArgument] = argv.slice(2)

if (!inputDirectoryArgument) {
  throw new Error(
    'Usage: node scripts/build-gymti-illustrations.mjs <source-image-directory> [output-directory]',
  )
}

const scriptDirectory = fileURLToPath(new URL('.', import.meta.url))
const inputDirectory = resolve(inputDirectoryArgument)
const outputDirectory = outputDirectoryArgument
  ? resolve(outputDirectoryArgument)
  : resolve(scriptDirectory, '../public/gymti/types')

const sourceSize = 1254
const cropHeight = 1024
const outputWidth = 720

await mkdir(outputDirectory, { recursive: true })

for (const [typeId, sourceFile] of Object.entries(sourceFiles)) {
  const inputPath = resolve(inputDirectory, sourceFile)
  const metadata = await sharp(inputPath).metadata()

  if (metadata.width !== sourceSize || metadata.height !== sourceSize) {
    throw new Error(
      `${sourceFile} must be ${sourceSize}x${sourceSize}; received ${metadata.width}x${metadata.height}`,
    )
  }

  await sharp(inputPath)
    .extract({ left: 0, top: 0, width: sourceSize, height: cropHeight })
    .resize({ width: outputWidth, withoutEnlargement: true })
    .webp({ quality: 82, effort: 6, smartSubsample: true })
    .toFile(resolve(outputDirectory, `${typeId}.webp`))
}

stdout.write(`Built ${Object.keys(sourceFiles).length} GYMTI illustrations in ${outputDirectory}\n`)
