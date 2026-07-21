import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  AnalysisApiError,
  accessClient,
  analysisClient,
} from '@/api/client'

describe('API client', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('starts explicit whole-video analysis without sending playback time', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ id: 'run-1' }),
      { status: 202, headers: { 'Content-Type': 'application/json' } },
    ))
    vi.stubGlobal('fetch', fetchMock)

    await analysisClient.createRun('arm-01')

    expect(fetchMock).toHaveBeenCalledWith('/api/v1/analysis-runs', expect.objectContaining({
      method: 'POST',
      credentials: 'same-origin',
      body: JSON.stringify({ source_id: 'arm-01' }),
    }))
  })

  it('keeps Retry-After when analysis capacity is unavailable', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: '真实动作分析暂时繁忙，请稍后重试' }),
      {
        status: 429,
        headers: {
          'Content-Type': 'application/json',
          'Retry-After': '15',
        },
      },
    )))

    await expect(analysisClient.createRun('arm-01')).rejects.toMatchObject({
      status: 429,
      retryAfterSeconds: 15,
    } satisfies Partial<AnalysisApiError>)
  })

  it('upgrades access using a same-origin credentialed request', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      tier: 'judge',
      can_analyze: true,
      retry_after_seconds: null,
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(accessClient.upgrade('review-code')).resolves.toMatchObject({
      tier: 'judge',
      can_analyze: true,
    })
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/access/session', expect.objectContaining({
      method: 'POST',
      credentials: 'same-origin',
      body: JSON.stringify({ access_code: 'review-code' }),
    }))
  })
})
