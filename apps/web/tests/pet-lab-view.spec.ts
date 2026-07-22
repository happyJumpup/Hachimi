import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import PetLabView from '@/views/PetLabView.vue'

describe('TrainPal pet development lab', () => {
  it('shows every existing style action without a selection or persistence control', () => {
    const wrapper = mount(PetLabView)

    expect(wrapper.findAll('[data-testid="pet-style"]')).toHaveLength(7)
    expect(wrapper.findAll('[data-testid="pet-action"]')).toHaveLength(28)
    expect(wrapper.findAll('button')).toHaveLength(0)
    expect(wrapper.findAll('input')).toHaveLength(0)
    expect(wrapper.text()).toContain('仅用于开发验收')
  })
})
