import { describe, expect, it } from 'vitest'

import { validateLocalMediaFile } from '@/domain/local-media'

const capabilities = {
  local_upload_enabled: true,
  local_analysis_max_seconds: 60,
  local_upload_max_bytes: 10,
}

describe('local media file gate', () => {
  it('accepts a supported file inside the deployed size and duration limits', () => {
    const file = new File(['12345'], '训练.mp4', { type: 'video/mp4' })
    expect(validateLocalMediaFile(file, 59.9, capabilities)).toEqual({ ok: true })
  })

  it('rejects unsupported type, bytes, and duration with user-safe deployed limits', () => {
    expect(validateLocalMediaFile(
      new File(['x'], '训练.avi', { type: 'video/x-msvideo' }),
      10,
      capabilities,
    )).toMatchObject({ ok: false, code: 'unsupported_type' })
    expect(validateLocalMediaFile(
      new File(['12345678901'], '训练.mp4', { type: 'video/mp4' }),
      10,
      capabilities,
    )).toMatchObject({ ok: false, code: 'too_large' })
    expect(validateLocalMediaFile(
      new File(['x'], '训练.mp4', { type: 'video/mp4' }),
      61,
      capabilities,
    )).toEqual({
      ok: false,
      code: 'too_long',
      message: '当前环境支持最长 1 分钟的视频；长视频能力尚未完成验证',
    })
  })
})
