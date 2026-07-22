import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import GymtiProfileStep from '@/features/gymti/GymtiProfileStep.vue'

const emptyProfile = {
  id: 'current' as const,
  sex: null,
  age: null,
  heightCm: null,
  weightKg: null,
  updatedAt: new Date(0).toISOString(),
}

describe('GYMTI optional training profile step', () => {
  it('keeps every field optional and emits a single profile save', async () => {
    const wrapper = mount(GymtiProfileStep, {
      props: { profile: emptyProfile, busy: false, saveFailed: false },
    })

    expect(wrapper.text()).toContain('可跳过，不影响测评结果')
    expect(wrapper.text()).not.toContain('个性化约值已开启')
    expect(wrapper.find('[data-action="clear"]').exists()).toBe(false)
    await wrapper.get('input[aria-label="年龄"]').setValue('28')
    await wrapper.get('select[aria-label="性别"]').setValue('female')
    await wrapper.get('form').trigger('submit')

    expect(wrapper.emitted('save')?.[0]).toEqual([{
      sex: 'female',
      age: 28,
      heightCm: null,
      weightKg: null,
    }])
  })

  it('offers retry and continue without saving after a persistence failure', async () => {
    const wrapper = mount(GymtiProfileStep, {
      props: {
        profile: { ...emptyProfile, heightCm: 168 },
        busy: false,
        saveFailed: true,
      },
    })

    expect(wrapper.text()).toContain('训练档案暂时没有保存成功')
    expect(wrapper.find('[data-action="clear"]').exists()).toBe(true)
    await wrapper.get('[data-action="retry"]').trigger('click')
    await wrapper.get('[data-action="continue"]').trigger('click')

    expect(wrapper.emitted('save')).toHaveLength(1)
    expect(wrapper.emitted('skip')).toHaveLength(1)
  })
})
