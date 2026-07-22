import { describe, expect, it, vi } from 'vitest'

import {
  LocalDataCoordinationUnavailableError,
  createLocalDataClearCoordinator,
  type ClearChannel,
  type LocalDataClearParticipant,
} from '@/local-data/clear-coordinator'
import { LocalDataEpochFence, type EpochStorage } from '@/local-data/epoch-fence'

class MemoryEpochStorage implements EpochStorage {
  private readonly values = new Map<string, string>()

  getItem(key: string): string | null {
    return this.values.get(key) ?? null
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value)
  }
}

class ChannelHub {
  private readonly channels = new Set<MemoryChannel>()

  create = (): ClearChannel => {
    const channel = new MemoryChannel(this)
    this.channels.add(channel)
    return channel
  }

  broadcast(sender: MemoryChannel, data: unknown): void {
    for (const channel of this.channels) {
      if (channel !== sender) channel.receive(data)
    }
  }

  close(channel: MemoryChannel): void {
    this.channels.delete(channel)
  }
}

class MemoryChannel implements ClearChannel {
  onmessage: ((event: MessageEvent<unknown>) => void) | null = null

  constructor(private readonly hub: ChannelHub) {}

  postMessage(data: unknown): void {
    this.hub.broadcast(this, structuredClone(data))
  }

  receive(data: unknown): void {
    queueMicrotask(() => this.onmessage?.(new MessageEvent('message', { data })))
  }

  close(): void {
    this.hub.close(this)
  }
}

const participant = (
  overrides: Partial<LocalDataClearParticipant> = {},
): LocalDataClearParticipant => ({
  prepare: async () => undefined,
  commit: async () => undefined,
  abort: async () => undefined,
  ...overrides,
})

describe('同源标签页清空协调', () => {
  it('waits for another tab pending draft and preference writes before the six-table clear', async () => {
    const hub = new ChannelHub()
    const storage = new MemoryEpochStorage()
    const fenceA = new LocalDataEpochFence(storage)
    const fenceB = new LocalDataEpochFence(storage)
    const data: { draft: string | null; preference: boolean | null } = {
      draft: '待保存动作',
      preference: true,
    }
    let releasePreference!: () => void
    const preferenceWrite = new Promise<void>((resolve) => {
      releasePreference = () => {
        data.preference = false
        resolve()
      }
    })
    const pendingDraft = setTimeout(() => {
      fenceB.assertWritable()
      data.draft = '过期草稿'
    }, 50)
    const clearDatabase = vi.fn(async () => {
      data.draft = null
      data.preference = null
    })

    const coordinatorA = createLocalDataClearCoordinator({
      channelFactory: hub.create,
      fence: fenceA,
      tabId: 'tab-a',
      discoveryMilliseconds: 0,
      acknowledgementMilliseconds: 100,
      epochFactory: () => 'epoch-2',
    })
    const coordinatorB = createLocalDataClearCoordinator({
      channelFactory: hub.create,
      fence: fenceB,
      tabId: 'tab-b',
      discoveryMilliseconds: 0,
      acknowledgementMilliseconds: 100,
    })
    coordinatorA.connect(participant())
    coordinatorB.connect(participant({
      prepare: async () => {
        clearTimeout(pendingDraft)
        await preferenceWrite
      },
    }))

    const clearing = coordinatorA.clear(clearDatabase)
    await new Promise((resolve) => setTimeout(resolve, 5))

    expect(clearDatabase).not.toHaveBeenCalled()
    expect(() => fenceB.assertWritable()).toThrow(/stale local data epoch/)

    releasePreference()
    await clearing
    await new Promise((resolve) => setTimeout(resolve, 60))

    expect(clearDatabase).toHaveBeenCalledTimes(1)
    expect(data).toEqual({ draft: null, preference: null })
    expect(() => fenceB.assertWritable()).not.toThrow()
    coordinatorA.close()
    coordinatorB.close()
  })

  it('fails before touching local data when BroadcastChannel is unavailable', async () => {
    const prepare = vi.fn(async () => undefined)
    const clearDatabase = vi.fn(async () => undefined)
    const coordinator = createLocalDataClearCoordinator({
      channelFactory: null,
      fence: new LocalDataEpochFence(new MemoryEpochStorage()),
    })
    coordinator.connect(participant({ prepare }))

    await expect(coordinator.clear(clearDatabase)).rejects.toBeInstanceOf(
      LocalDataCoordinationUnavailableError,
    )
    expect(prepare).not.toHaveBeenCalled()
    expect(clearDatabase).not.toHaveBeenCalled()
  })
})
