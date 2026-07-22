import { describe, expect, it, vi } from 'vitest'

import {
  buildCompletionPosterModel,
  deliverCompletionPoster,
  renderCompletionPoster,
  type PosterDrawingContext,
  type PosterRuntime,
} from '@/features/experience/poster'

const completedResult = {
  outcome: 'completed' as const,
  planName: '手臂唤醒计划',
  trainingDurationSeconds: 24 * 60,
  caloriesKcal: 126,
  completedActionCount: 3,
  coachStyleId: null,
}

describe('completion poster', () => {
  it('uses only the approved public fields', () => {
    expect(buildCompletionPosterModel(completedResult)).toEqual({
      title: '训练完成',
      planName: '手臂唤醒计划',
      durationLabel: '24 分钟',
      caloriesLabel: '约 126 千卡',
      actionCountLabel: '完成 3 个动作',
    })
  })

  it('refuses to create a completion poster for an early-ended record', () => {
    expect(() =>
      buildCompletionPosterModel({ ...completedResult, outcome: 'ended_early' as const }),
    ).toThrow('只有完整训练可以生成完成海报')
  })

  it('renders a 1080 by 1920 PNG through an injectable browser boundary', async () => {
    const context = {
      fillRect: vi.fn(),
      fillText: vi.fn(),
      drawImage: vi.fn(),
    } as unknown as PosterDrawingContext
    const png = new Blob(['png'], { type: 'image/png' })
    const exportPng = vi.fn().mockResolvedValue(png)
    const runtime: PosterRuntime = {
      createSurface: vi.fn().mockReturnValue({ context, exportPng }),
      loadImage: vi.fn().mockResolvedValue({}),
    }

    await expect(renderCompletionPoster(completedResult, runtime)).resolves.toBe(png)
    expect(runtime.createSurface).toHaveBeenCalledWith(1080, 1920)
    expect(runtime.loadImage).not.toHaveBeenCalled()
    expect(context.drawImage).not.toHaveBeenCalled()
    expect(exportPng).toHaveBeenCalledTimes(1)
  })

  it('draws the completed action frame only when the record has a confirmed style', async () => {
    const context = {
      fillRect: vi.fn(),
      fillText: vi.fn(),
      drawImage: vi.fn(),
    } as unknown as PosterDrawingContext
    const runtime: PosterRuntime = {
      createSurface: vi.fn().mockReturnValue({
        context,
        exportPng: vi.fn().mockResolvedValue(new Blob(['png'], { type: 'image/png' })),
      }),
      loadImage: vi.fn().mockResolvedValue({}),
    }

    await renderCompletionPoster({ ...completedResult, coachStyleId: 'gentle' }, runtime)

    expect(runtime.loadImage).toHaveBeenCalledWith('/trainpal/pets/gentle/comfort/01.webp')
    expect(context.drawImage).toHaveBeenCalledTimes(1)
  })

  it('uses Web Share when file sharing is available', async () => {
    const share = vi.fn().mockResolvedValue(undefined)
    const download = vi.fn()
    const result = await deliverCompletionPoster(new Blob(['png'], { type: 'image/png' }), '手臂计划', {
      canShare: () => true,
      share,
      download,
    })

    expect(result).toBe('shared')
    expect(share).toHaveBeenCalledTimes(1)
    expect(download).not.toHaveBeenCalled()
  })

  it('downloads the PNG when Web Share is unavailable or fails', async () => {
    const download = vi.fn()
    const result = await deliverCompletionPoster(new Blob(['png'], { type: 'image/png' }), '手臂/计划', {
      canShare: () => false,
      share: vi.fn(),
      download,
    })

    expect(result).toBe('downloaded')
    expect(download).toHaveBeenCalledWith(expect.any(File), 'TrainPal-手臂-计划.png')
  })
})
