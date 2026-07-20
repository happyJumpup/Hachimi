import { describe, expect, it, vi } from 'vitest'

import {
  buildCompletionPosterModel,
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
    expect(runtime.loadImage).toHaveBeenCalledTimes(1)
    expect(context.drawImage).toHaveBeenCalledTimes(1)
    expect(exportPng).toHaveBeenCalledTimes(1)
  })
})
