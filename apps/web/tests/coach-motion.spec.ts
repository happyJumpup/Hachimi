import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import CoachMotion from '@/features/experience/CoachMotion.vue'

const normalMotion = {
  matches: false,
  media: '(prefers-reduced-motion: reduce)',
  onchange: null,
  addEventListener: vi.fn(),
  removeEventListener: vi.fn(),
  addListener: vi.fn(),
  removeListener: vi.fn(),
  dispatchEvent: vi.fn(),
}

describe('CoachMotion', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.stubGlobal('matchMedia', vi.fn(() => normalMotion))
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('occupies no space without a confirmed style or when hidden', () => {
    const unconfirmed = mount(CoachMotion, {
      props: { styleId: null, state: 'idle', visible: true },
    })
    const hidden = mount(CoachMotion, {
      props: { styleId: 'gentle', state: 'idle', visible: false },
    })

    expect(unconfirmed.html()).toBe('<!--v-if-->')
    expect(hidden.html()).toBe('<!--v-if-->')
  })

  it('requests only the current style and action frame', async () => {
    const wrapper = mount(CoachMotion, {
      props: { styleId: 'gentle', state: 'resting' },
    })

    expect(wrapper.findAll('img')).toHaveLength(1)
    expect(wrapper.get('img').attributes('src')).toBe('/trainpal/pets/gentle/rest/01.webp')

    await vi.advanceTimersByTimeAsync(190)
    expect(wrapper.get('img').attributes('src')).toBe('/trainpal/pets/gentle/rest/02.webp')
  })

  it('plays one six-frame cue and then returns to the persistent base loop', async () => {
    const wrapper = mount(CoachMotion, {
      props: {
        styleId: 'hotblood',
        state: 'training',
        cue: { sequence: 1, event: 'set_started' },
      },
    })

    expect(wrapper.get('img').attributes('src')).toBe('/trainpal/pets/hotblood/cheer/01.webp')
    await vi.advanceTimersByTimeAsync(120 * 5)
    expect(wrapper.get('img').attributes('src')).toBe('/trainpal/pets/hotblood/cheer/06.webp')
    await vi.advanceTimersByTimeAsync(120)
    expect(wrapper.get('img').attributes('src')).toBe('/trainpal/pets/hotblood/idle/01.webp')
  })

  it('does not advance frames or autoplay cues with reduced motion enabled', async () => {
    vi.stubGlobal('matchMedia', vi.fn(() => ({ ...normalMotion, matches: true })))
    const wrapper = mount(CoachMotion, {
      props: {
        styleId: 'challenger',
        state: 'resting',
        cue: { sequence: 1, event: 'rest_final_countdown' },
      },
    })

    expect(wrapper.get('img').attributes('src')).toBe('/trainpal/pets/challenger/idle/01.webp')
    await vi.advanceTimersByTimeAsync(10_000)
    expect(wrapper.get('img').attributes('src')).toBe('/trainpal/pets/challenger/idle/01.webp')
  })

  it('keeps equivalent status text when a frame fails', async () => {
    const wrapper = mount(CoachMotion, {
      props: { styleId: 'zen', state: 'paused' },
    })
    await wrapper.get('img').trigger('error')

    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.text()).toContain('TrainPal 正在等你继续')
  })

  it('allows the development lab to loop an existing non-automatic action', () => {
    const wrapper = mount(CoachMotion, {
      props: { styleId: 'snarky', state: 'idle', previewAction: 'mock' },
    })

    expect(wrapper.get('img').attributes('src')).toBe('/trainpal/pets/snarky/mock/01.webp')
    expect(wrapper.get('.coach-motion').attributes('data-action')).toBe('mock')
  })
})
