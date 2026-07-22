import { describe, expect, it } from 'vitest'

import { derivePetState } from '@/features/experience/pet-state'

describe('Pet presentation state', () => {
  it('maps the training session contract without owning training logic', () => {
    expect(derivePetState({ sessionStatus: null })).toBe('idle')
    expect(derivePetState({ sessionStatus: 'active' })).toBe('training')
    expect(derivePetState({ sessionStatus: 'resting' })).toBe('resting')
    expect(derivePetState({ sessionStatus: 'ready_to_continue' })).toBe('paused')
    expect(derivePetState({ sessionStatus: 'paused', pauseReason: 'before_start' })).toBe('idle')
    expect(derivePetState({ sessionStatus: 'paused', pauseReason: 'between_actions' })).toBe('idle')
    expect(derivePetState({ sessionStatus: 'paused', pauseReason: 'page_hidden' })).toBe('paused')
  })

  it('uses the completed state only for a completed result', () => {
    expect(derivePetState({ sessionStatus: null, outcome: 'completed' })).toBe('completed')
    expect(derivePetState({ sessionStatus: null, outcome: 'ended_early' })).toBe('idle')
  })
})
