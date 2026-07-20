import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import HachimiPet from '@/features/experience/HachimiPet.vue'

describe('HachimiPet', () => {
  it('renders the selected presentation state and never exposes training controls', () => {
    const wrapper = mount(HachimiPet, { props: { state: 'training', visible: true } })

    expect(wrapper.get('img').attributes('alt')).toBe('哈肌咪正在陪你训练')
    expect(wrapper.find('button').exists()).toBe(false)
  })

  it('falls back to equivalent status text when the asset fails', async () => {
    const wrapper = mount(HachimiPet, { props: { state: 'resting', visible: true } })
    await wrapper.get('img').trigger('error')

    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.text()).toContain('哈肌咪正在休息，训练不受影响')
  })

  it('occupies no space when the persisted preference hides it', () => {
    const wrapper = mount(HachimiPet, { props: { state: 'idle', visible: false } })

    expect(wrapper.html()).toBe('<!--v-if-->')
  })
})
