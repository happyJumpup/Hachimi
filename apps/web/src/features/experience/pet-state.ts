export type PetState = 'idle' | 'training' | 'resting' | 'paused' | 'completed'
export type TrainingSessionStatus = 'active' | 'resting' | 'ready_to_continue' | 'paused'
export type PauseReason = 'before_start' | 'user' | 'page_hidden' | 'recovered' | 'between_actions'
export type TrainingOutcome = 'completed' | 'ended_early'

export interface PetStateInput {
  sessionStatus: TrainingSessionStatus | null
  pauseReason?: PauseReason | null
  outcome?: TrainingOutcome | null
}

export function derivePetState(input: PetStateInput): PetState {
  if (input.outcome === 'completed') return 'completed'

  switch (input.sessionStatus) {
    case 'active':
      return 'training'
    case 'resting':
      return 'resting'
    case 'ready_to_continue':
      return 'paused'
    case 'paused':
      return input.pauseReason === 'before_start' || input.pauseReason === 'between_actions'
        ? 'idle'
        : 'paused'
    case null:
      return 'idle'
  }
}
