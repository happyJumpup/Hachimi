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

  it('loads the current local-upload limits from capabilities', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      local_upload_enabled: true,
      local_analysis_max_seconds: 300,
      local_upload_max_bytes: 25_000_000,
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(analysisClient.getCapabilities()).resolves.toEqual({
      local_upload_enabled: true,
      local_analysis_max_seconds: 300,
      local_upload_max_bytes: 25_000_000,
    })
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/capabilities', expect.objectContaining({
      credentials: 'same-origin',
    }))
  })

  it('uploads local media as multipart without overriding its content type', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ id: 'run-local' }),
      { status: 202, headers: { 'Content-Type': 'application/json' } },
    ))
    vi.stubGlobal('fetch', fetchMock)
    const file = new File(['video'], '训练.mp4', { type: 'video/mp4' })

    await analysisClient.createLocalRun({
      file,
      localSourceId: 'local:11111111-1111-4111-8111-111111111111',
    })

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/v1/analysis-runs/local')
    expect(init.method).toBe('POST')
    expect(init.headers).not.toMatchObject({ 'Content-Type': expect.anything() })
    const body = init.body as FormData
    expect(body.get('media')).toBe(file)
    expect(body.get('local_source_id')).toBe('local:11111111-1111-4111-8111-111111111111')
  })

  it('uploads only the requested absolute range when retrying a coverage gap', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ id: 'run-gap' }),
      { status: 202, headers: { 'Content-Type': 'application/json' } },
    ))
    vi.stubGlobal('fetch', fetchMock)

    await analysisClient.createLocalRun({
      file: new File(['video'], '训练.mp4', { type: 'video/mp4' }),
      localSourceId: 'local:11111111-1111-4111-8111-111111111111',
      range: { start_seconds: 30, end_seconds: 45 },
    })

    const body = fetchMock.mock.calls[0]?.[1]?.body as FormData
    expect(body.get('range_start_seconds')).toBe('30')
    expect(body.get('range_end_seconds')).toBe('45')
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
