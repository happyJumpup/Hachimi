export const LOCAL_DATA_CLEAR_EPOCH_KEY = 'hachimi-fitness:clear-epoch'

export interface EpochStorage {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
}

const readBrowserStorage = (): EpochStorage | null => {
  try {
    return globalThis.localStorage ?? null
  } catch {
    return null
  }
}

export class LocalDataEpochFence {
  private expectedEpoch: string

  constructor(
    private readonly storage: EpochStorage | null = readBrowserStorage(),
    private readonly key = LOCAL_DATA_CLEAR_EPOCH_KEY,
  ) {
    this.expectedEpoch = this.readEpoch()
  }

  get available(): boolean {
    return this.storage !== null
  }

  private readEpoch(): string {
    return this.storage?.getItem(this.key) ?? '0'
  }

  beginClear(epoch: string): void {
    if (!this.storage) throw new Error('local data epoch storage unavailable')
    this.storage.setItem(this.key, epoch)
    this.expectedEpoch = epoch
  }

  adopt(epoch: string): void {
    this.expectedEpoch = epoch
  }

  assertWritable(): void {
    if (!this.storage) return
    if (this.readEpoch() !== this.expectedEpoch) {
      throw new Error('stale local data epoch')
    }
  }
}

export const localDataEpochFence = new LocalDataEpochFence()
