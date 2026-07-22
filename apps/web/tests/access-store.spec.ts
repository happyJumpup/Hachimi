import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'

import { AnalysisApiError, type AccessClient } from '@/api/client'
import type { AccessSession } from '@/domain/types'
import { useAccessStore } from '@/stores/access'

class FakeAccessClient implements AccessClient {
  session: AccessSession = {
    tier: 'public',
    can_analyze: false,
    retry_after_seconds: null,
  }

  async getSession(): Promise<AccessSession> {
    return this.session
  }

  async upgrade(accessCode: string): Promise<AccessSession> {
    if (accessCode !== 'valid-code') {
      throw new AnalysisApiError('体验码无效', 401, null)
    }
    this.session = {
      tier: 'judge',
      can_analyze: true,
      retry_after_seconds: null,
    }
    return this.session
  }
}

describe('access store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('keeps capacity status separate from provider failures', async () => {
    const client = new FakeAccessClient()
    const store = useAccessStore()

    await store.load(client)

    expect(store.tier).toBe('public')
    expect(store.canAnalyze).toBe(false)
    expect(store.message).toBe('公开实时 AI 名额暂不可用')
  })

  it('upgrades a valid judge code without retaining the code', async () => {
    const client = new FakeAccessClient()
    const store = useAccessStore()

    await expect(store.upgrade('valid-code', client)).resolves.toBe(true)

    expect(store.tier).toBe('judge')
    expect(store.canAnalyze).toBe(true)
    expect(store.errorMessage).toBe('')
    expect(JSON.stringify(store.$state)).not.toContain('valid-code')
  })

  it('shows the safe invalid-code error and remains public', async () => {
    const client = new FakeAccessClient()
    const store = useAccessStore()

    await expect(store.upgrade('wrong-code', client)).resolves.toBe(false)

    expect(store.tier).toBe('public')
    expect(store.errorMessage).toBe('体验码无效')
  })

  it.each([
    [403, null, '当前页面来源无效，请从正式入口重新打开'],
    [429, 75, '体验码尝试过于频繁，请 75 秒后重试'],
    [429, null, '体验码尝试过于频繁，请稍后重试'],
    [422, null, '体验码格式无效'],
  ] as const)(
    'maps access upgrade status %s to an actionable message',
    async (status, retryAfterSeconds, expectedMessage) => {
      const client: AccessClient = {
        getSession: async () => ({
          tier: 'public',
          can_analyze: false,
          retry_after_seconds: null,
        }),
        upgrade: async () => {
          throw new AnalysisApiError('safe server error', status, retryAfterSeconds)
        },
      }
      const store = useAccessStore()

      await expect(store.upgrade('submitted-code', client)).resolves.toBe(false)

      expect(store.tier).toBe('public')
      expect(store.pending).toBe(false)
      expect(store.errorMessage).toBe(expectedMessage)
    },
  )

  it('keeps the generic retry message for network failures', async () => {
    const client: AccessClient = {
      getSession: async () => ({
        tier: 'public',
        can_analyze: false,
        retry_after_seconds: null,
      }),
      upgrade: async () => {
        throw new TypeError('network unavailable')
      },
    }
    const store = useAccessStore()

    await expect(store.upgrade('submitted-code', client)).resolves.toBe(false)

    expect(store.errorMessage).toBe('体验码校验失败，请稍后重试')
  })
})
