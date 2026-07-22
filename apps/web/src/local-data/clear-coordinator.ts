import { localDataEpochFence, type LocalDataEpochFence } from '@/local-data/epoch-fence'

const CHANNEL_NAME = 'hachimi-fitness:local-data-clear'

type ClearMessage =
  | { type: 'discover'; from: string; requestId: string }
  | { type: 'present'; from: string; requestId: string }
  | { type: 'prepare'; from: string; requestId: string; epoch: string }
  | { type: 'prepared'; from: string; requestId: string; epoch: string }
  | { type: 'commit'; from: string; epoch: string }
  | { type: 'abort'; from: string; epoch: string }

export interface ClearChannel {
  onmessage: ((event: MessageEvent<unknown>) => void) | null
  postMessage(data: unknown): void
  close(): void
}

export interface LocalDataClearParticipant {
  prepare(epoch: string): Promise<void>
  commit(epoch: string): Promise<void> | void
  abort(epoch: string): Promise<void> | void
}

export interface LocalDataClearCoordinator {
  readonly supported: boolean
  connect(participant: LocalDataClearParticipant): void
  clear(clearDatabase: () => Promise<void>): Promise<void>
  close(): void
}

interface CoordinatorOptions {
  channelFactory?: (() => ClearChannel) | null
  fence?: LocalDataEpochFence
  tabId?: string
  discoveryMilliseconds?: number
  acknowledgementMilliseconds?: number
  epochFactory?: () => string
}

export class LocalDataCoordinationUnavailableError extends Error {
  constructor() {
    super('safe local data coordination is unavailable')
    this.name = 'LocalDataCoordinationUnavailableError'
  }
}

const browserChannelFactory = (): ClearChannel | null => {
  if (typeof globalThis.BroadcastChannel !== 'function') return null
  return new globalThis.BroadcastChannel(CHANNEL_NAME)
}

const delay = (milliseconds: number): Promise<void> =>
  new Promise((resolve) => setTimeout(resolve, milliseconds))

const isMessage = (value: unknown): value is ClearMessage => {
  if (!value || typeof value !== 'object') return false
  const type = (value as { type?: unknown }).type
  const from = (value as { from?: unknown }).from
  return typeof type === 'string' && typeof from === 'string'
}

export const createLocalDataClearCoordinator = ({
  channelFactory,
  fence = localDataEpochFence,
  tabId = globalThis.crypto?.randomUUID?.() ?? `tab-${Date.now()}-${Math.random()}`,
  discoveryMilliseconds = 75,
  acknowledgementMilliseconds = 2_000,
  epochFactory = () => `${Date.now()}-${globalThis.crypto?.randomUUID?.() ?? Math.random()}`,
}: CoordinatorOptions = {}): LocalDataClearCoordinator => {
  let channel: ClearChannel | null = null
  if (channelFactory !== null) {
    try {
      channel = channelFactory ? channelFactory() : browserChannelFactory()
    } catch {
      channel = null
    }
  }

  let participant: LocalDataClearParticipant | null = null
  let incoming = Promise.resolve()
  let discovery: { requestId: string; peers: Set<string> } | null = null
  let pendingAcknowledgements: {
    requestId: string
    epoch: string
    peers: Set<string>
    resolve: () => void
  } | null = null
  let clearing = false

  const post = (message: ClearMessage): void => channel?.postMessage(message)

  const handle = async (message: ClearMessage): Promise<void> => {
    if (message.from === tabId) return

    if (message.type === 'discover') {
      post({ type: 'present', from: tabId, requestId: message.requestId })
      return
    }
    if (message.type === 'present') {
      if (discovery?.requestId === message.requestId) discovery.peers.add(message.from)
      return
    }
    if (message.type === 'prepared') {
      const pending = pendingAcknowledgements
      if (pending?.requestId !== message.requestId || pending.epoch !== message.epoch) return
      pending.peers.delete(message.from)
      if (!pending.peers.size) pending.resolve()
      return
    }
    if (message.type === 'prepare') {
      await participant?.prepare(message.epoch)
      post({
        type: 'prepared',
        from: tabId,
        requestId: message.requestId,
        epoch: message.epoch,
      })
      return
    }
    if (message.type === 'commit') {
      fence.adopt(message.epoch)
      await participant?.commit(message.epoch)
      return
    }
    if (message.type === 'abort') {
      fence.adopt(message.epoch)
      await participant?.abort(message.epoch)
    }
  }

  if (channel) {
    channel.onmessage = (event) => {
      if (!isMessage(event.data)) return
      incoming = incoming.then(() => handle(event.data as ClearMessage), () => handle(event.data as ClearMessage))
    }
  }

  const waitForAcknowledgements = (
    requestId: string,
    epoch: string,
    peers: Set<string>,
  ): Promise<void> => {
    if (!peers.size) return Promise.resolve()
    return new Promise<void>((resolve, reject) => {
      const timeout = setTimeout(() => {
        pendingAcknowledgements = null
        reject(new Error('local data coordination timed out'))
      }, acknowledgementMilliseconds)
      pendingAcknowledgements = {
        requestId,
        epoch,
        peers,
        resolve: () => {
          clearTimeout(timeout)
          pendingAcknowledgements = null
          resolve()
        },
      }
    })
  }

  return {
    get supported() {
      return channel !== null && fence.available
    },

    connect(nextParticipant) {
      participant = nextParticipant
    },

    async clear(clearDatabase) {
      if (!channel || !fence.available || !participant || clearing) {
        throw new LocalDataCoordinationUnavailableError()
      }
      clearing = true
      try {
        const requestId = `${tabId}-${Date.now()}-${Math.random()}`
        const peers = new Set<string>()
        discovery = { requestId, peers }
        post({ type: 'discover', from: tabId, requestId })
        await delay(discoveryMilliseconds)
        discovery = null

        const epoch = epochFactory()
        try {
          fence.beginClear(epoch)
        } catch {
          throw new LocalDataCoordinationUnavailableError()
        }
        const localPreparation = participant.prepare(epoch)
        post({ type: 'prepare', from: tabId, requestId, epoch })

        try {
          await Promise.all([
            localPreparation,
            waitForAcknowledgements(requestId, epoch, new Set(peers)),
          ])
          await clearDatabase()
          fence.adopt(epoch)
          await participant.commit(epoch)
          post({ type: 'commit', from: tabId, epoch })
        } catch (error) {
          fence.adopt(epoch)
          await participant.abort(epoch)
          post({ type: 'abort', from: tabId, epoch })
          throw error
        }
      } finally {
        discovery = null
        clearing = false
      }
    },

    close() {
      channel?.close()
      channel = null
    },
  }
}

export const localDataClearCoordinator = createLocalDataClearCoordinator()
