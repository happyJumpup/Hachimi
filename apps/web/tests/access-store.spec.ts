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
})
