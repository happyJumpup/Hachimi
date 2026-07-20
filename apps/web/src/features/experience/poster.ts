export type PosterOutcome = 'completed' | 'ended_early'

export interface CompletionPosterInput {
  outcome: PosterOutcome
  planName: string
  trainingDurationSeconds: number
  caloriesKcal: number
  completedActionCount: number
}

export interface CompletionPosterModel {
  title: '训练完成'
  planName: string
  durationLabel: string
  caloriesLabel: string
  actionCountLabel: string
}

export interface PosterDrawingContext {
  fillStyle: string | CanvasGradient | CanvasPattern
  font: string
  textAlign: CanvasTextAlign
  textBaseline: CanvasTextBaseline
  fillRect(x: number, y: number, width: number, height: number): void
  fillText(text: string, x: number, y: number, maxWidth?: number): void
  drawImage(image: CanvasImageSource, dx: number, dy: number, dWidth: number, dHeight: number): void
}

export interface PosterSurface {
  context: PosterDrawingContext
  exportPng(): Promise<Blob>
}

export interface PosterRuntime {
  createSurface(width: number, height: number): PosterSurface
  loadImage(url: string): Promise<CanvasImageSource>
}

const POSTER_WIDTH = 1_080
const POSTER_HEIGHT = 1_920
const COMPLETED_PET_URL = new URL('../../assets/pet/completed.webp', import.meta.url).href

function asNonNegativeInteger(value: number): number {
  if (!Number.isFinite(value) || value < 0) throw new Error('海报数据不完整')
  return Math.round(value)
}

export function buildCompletionPosterModel(input: CompletionPosterInput): CompletionPosterModel {
  if (input.outcome !== 'completed') throw new Error('只有完整训练可以生成完成海报')

  const planName = input.planName.trim()
  if (!planName) throw new Error('海报数据不完整')

  const durationSeconds = asNonNegativeInteger(input.trainingDurationSeconds)
  const caloriesKcal = asNonNegativeInteger(input.caloriesKcal)
  const completedActionCount = asNonNegativeInteger(input.completedActionCount)

  return {
    title: '训练完成',
    planName,
    durationLabel: `${Math.round(durationSeconds / 60)} 分钟`,
    caloriesLabel: `约 ${caloriesKcal} 千卡`,
    actionCountLabel: `完成 ${completedActionCount} 个动作`,
  }
}

function createBrowserRuntime(): PosterRuntime {
  return {
    createSurface(width, height) {
      const canvas = document.createElement('canvas')
      canvas.width = width
      canvas.height = height
      const context = canvas.getContext('2d')
      if (!context) throw new Error('当前浏览器无法生成海报')

      return {
        context,
        exportPng: () =>
          new Promise<Blob>((resolve, reject) => {
            canvas.toBlob((blob) => {
              if (blob) resolve(blob)
              else reject(new Error('海报生成失败，请重试'))
            }, 'image/png')
          }),
      }
    },
    loadImage(url) {
      return new Promise<CanvasImageSource>((resolve, reject) => {
        const image = new Image()
        image.onload = () => resolve(image)
        image.onerror = () => reject(new Error('哈肌咪素材加载失败，请重试'))
        image.src = url
      })
    },
  }
}

function drawPoster(context: PosterDrawingContext, model: CompletionPosterModel, pet: CanvasImageSource): void {
  context.fillStyle = '#07090b'
  context.fillRect(0, 0, POSTER_WIDTH, POSTER_HEIGHT)
  context.fillStyle = '#0f1719'
  context.fillRect(72, 84, 936, 1_752)
  context.fillStyle = '#26ebd5'
  context.fillRect(72, 84, 18, 1_752)
  context.fillStyle = '#ff6f61'
  context.fillRect(90, 84, 918, 18)

  context.textAlign = 'left'
  context.textBaseline = 'alphabetic'
  context.fillStyle = '#26ebd5'
  context.font = '700 88px "Arial Narrow", "Microsoft YaHei", sans-serif'
  context.fillText(model.title, 144, 310, 780)
  context.fillStyle = '#f5f7f6'
  context.font = '700 74px "Microsoft YaHei", sans-serif'
  context.fillText(model.planName, 144, 470, 792)

  context.fillStyle = '#9eaaa7'
  context.font = '500 38px "Microsoft YaHei", sans-serif'
  context.fillText('本次训练', 144, 670)
  context.fillStyle = '#f5f7f6'
  context.font = '700 64px "Arial Narrow", "Microsoft YaHei", sans-serif'
  context.fillText(model.durationLabel, 144, 760)
  context.fillText(model.caloriesLabel, 144, 890)
  context.fillText(model.actionCountLabel, 144, 1_020)

  context.drawImage(pet, 570, 1_110, 360, 360)
  context.fillStyle = '#9eaaa7'
  context.font = '500 34px "Microsoft YaHei", sans-serif'
  context.fillText('哈基米练臂力动', 144, 1_690)
  context.fillStyle = '#26ebd5'
  context.font = '700 34px "Microsoft YaHei", sans-serif'
  context.fillText('继续找动作，下一练见', 144, 1_765)
}

export async function renderCompletionPoster(
  input: CompletionPosterInput,
  runtime: PosterRuntime = createBrowserRuntime(),
): Promise<Blob> {
  const model = buildCompletionPosterModel(input)
  const surface = runtime.createSurface(POSTER_WIDTH, POSTER_HEIGHT)
  const pet = await runtime.loadImage(COMPLETED_PET_URL)
  drawPoster(surface.context, model, pet)
  return surface.exportPng()
}
