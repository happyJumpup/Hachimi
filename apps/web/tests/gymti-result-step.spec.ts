import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import GymtiResultStep from '@/features/gymti/GymtiResultStep.vue'

const current = {
  id: 'current' as const,
  resultId: 'result-1',
  attemptId: 'attempt-1',
  questionnaireVersion: 'gymti-questionnaire.v1',
  scoringVersion: 'gymti-scoring.v1',
  answers: [{ questionId: 'q1', optionId: 'q1-a' }],
  result: {
    gymtiType: 'LIFE' as const,
    secondaryGymtiType: 'HIDE' as const,
    recommendedCoachStyleId: 'gentle' as const,
    reasonCodes: ['goal_daily_energy', 'pref_supportive'],
    excludedCoachStyleIds: [],
  },
  narrative: {
    text: '你更在意训练能不能稳稳回到生活，而不是一次把自己耗尽。',
    source: 'template' as const,
    version: 'narrative.v1',
    model: null,
    generatedAt: '2026-07-23T00:08:00.000Z',
  },
  createdAt: '2026-07-23T00:05:00.000Z',
  updatedAt: '2026-07-23T00:10:00.000Z',
  activatedAt: '2026-07-23T00:10:00.000Z',
}

describe('GYMTI result step', () => {
  it('keeps GYMTI first, recommendation second, and one fixed primary action', async () => {
    const wrapper = mount(GymtiResultStep, {
      props: { current, confirmedStyleId: null, busy: false },
    })

    const sections = wrapper.findAll('[data-result-section]')
    expect(sections[0]?.attributes('data-result-section')).toBe('gymti')
    expect(sections[1]?.attributes('data-result-section')).toBe('coach')
    expect(wrapper.text()).toContain('续命打工人')
    expect(wrapper.text()).toContain('次要倾向 · 器械区躲猫猫小不点')
    expect(wrapper.text()).toContain('温柔陪伴型')
    expect(wrapper.findAll('[data-primary-action]')).toHaveLength(1)
    expect(wrapper.emitted('ready')).toHaveLength(1)

    await wrapper.get('[data-action="modify"]').trigger('click')
    await wrapper.get('[data-action="confirm"]').trigger('click')
    await wrapper.get('[data-action="retest"]').trigger('click')
    expect(wrapper.emitted('modify')).toHaveLength(1)
    expect(wrapper.emitted('confirm')).toHaveLength(1)
    expect(wrapper.emitted('retest')).toHaveLength(1)
  })
})
