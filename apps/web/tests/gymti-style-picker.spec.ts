import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import GymtiStylePicker from '@/features/gymti/GymtiStylePicker.vue'

describe('GYMTI coach style picker', () => {
  it('shows all seven styles but animates and confirms only the temporary selection', async () => {
    const wrapper = mount(GymtiStylePicker, {
      props: {
        recommendedStyleId: 'gentle',
        confirmedStyleId: 'zen',
        busy: false,
      },
    })

    expect(wrapper.findAll('[data-style-id]')).toHaveLength(7)
    expect(wrapper.findAllComponents({ name: 'CoachMotion' })).toHaveLength(1)
    expect(wrapper.get('[data-style-id="gentle"]').text()).toContain('本次推荐')
    expect(wrapper.get('[data-style-id="zen"]').text()).toContain('当前使用')

    await wrapper.get('[data-style-id="hotblood"]').trigger('click')
    expect(wrapper.emitted('confirm')).toBeUndefined()
    expect(wrapper.get('[data-style-id="hotblood"]').attributes('aria-pressed')).toBe('true')
    expect(wrapper.findAllComponents({ name: 'CoachMotion' })).toHaveLength(1)

    await wrapper.get('[data-action="confirm"]').trigger('click')
    expect(wrapper.emitted('confirm')).toEqual([['hotblood']])
  })
})
