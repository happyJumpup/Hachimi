import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import CandidateReviewPanel from '@/components/CandidateReviewPanel.vue'
import type { AnalysisCandidate } from '@/domain/types'

const candidates: AnalysisCandidate[] = [
  {
    id: 'candidate-a',
    name: '拖拽弯举',
    source_id: 'video-a',
    segment: { start_seconds: 41, end_seconds: 51 },
    parameters: {
      mode: null,
      sets: null,
      reps: null,
      duration_seconds: null,
      rest_seconds: null,
    },
    evidence: [{ type: 'visual', start_seconds: 41, end_seconds: 51 }],
    needs_confirmation: true,
  },
  {
    id: 'candidate-b',
    name: '锤式弯举',
    source_id: 'video-a',
    segment: { start_seconds: 54, end_seconds: 62 },
    parameters: {
      mode: 'reps',
      sets: null,
      reps: 12,
      duration_seconds: null,
      rest_seconds: null,
    },
    evidence: [{ type: 'speech', start_seconds: 54, end_seconds: 62 }],
    needs_confirmation: false,
  },
]

describe('CandidateReviewPanel', () => {
  it('lets the user return to the video without adding a candidate', async () => {
    const wrapper = mount(CandidateReviewPanel, {
      props: { candidates, warnings: [] },
    })

    await wrapper.get('button[aria-label="返回视频并重新选择时间点"]').trigger('click')

    expect(wrapper.emitted('close')).toEqual([[]])
    expect(wrapper.emitted('add')).toBeUndefined()
  })

  it('requires an unknown mode and emits only selected, edited candidates', async () => {
    const wrapper = mount(CandidateReviewPanel, {
      props: { candidates, warnings: [] },
    })
    const cards = wrapper.findAll('.candidate-card')
    const addButton = wrapper.get('.primary-action')

    expect(addButton.attributes('disabled')).toBeDefined()

    await cards[0].get('.action-name-input').setValue('单臂拖拽弯举')
    await cards[0].findAll('.segment-editor input')[0].setValue(42)
    await cards[0]
      .findAll('.mode-picker button')
      .find((button) => button.text() === '按次数')!
      .trigger('click')
    await cards[1].get<HTMLInputElement>('.candidate-select input').setValue(false)

    expect(addButton.attributes('disabled')).toBeUndefined()
    await addButton.trigger('click')

    const [[chosen, editedIds]] = wrapper.emitted<[
      AnalysisCandidate[],
      string[],
    ]>('add')!
    expect(chosen).toHaveLength(1)
    expect(chosen[0]).toMatchObject({
      id: 'candidate-a',
      name: '单臂拖拽弯举',
      segment: { start_seconds: 42, end_seconds: 51 },
      parameters: { mode: 'reps' },
    })
    expect(editedIds).toEqual(['candidate-a'])
    expect(candidates[0].name).toBe('拖拽弯举')
  })

  it('does not add a selected candidate with an empty name', async () => {
    const wrapper = mount(CandidateReviewPanel, {
      props: {
        candidates: [{ ...candidates[1], parameters: { ...candidates[1].parameters } }],
        warnings: [],
      },
    })

    await wrapper.get('.action-name-input').setValue('   ')

    expect(wrapper.get('.primary-action').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('动作名称不能为空')
  })

  it('does not add a video candidate without a preview segment', () => {
    const wrapper = mount(CandidateReviewPanel, {
      props: {
        candidates: [{
          ...candidates[1],
          segment: null,
          parameters: { ...candidates[1].parameters },
        } as unknown as AnalysisCandidate],
        warnings: [],
      },
    })

    expect(wrapper.get('.primary-action').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('这个候选缺少可预览片段，请重新分析')
  })

  it('does not add a candidate after its start time is cleared', async () => {
    const wrapper = mount(CandidateReviewPanel, {
      props: {
        candidates: [{ ...candidates[1], parameters: { ...candidates[1].parameters } }],
        warnings: [],
      },
    })

    await wrapper.get('.segment-editor input').setValue('')

    expect(wrapper.get('.primary-action').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('请填写有效时间，结束时间需晚于开始且不超过视频长度')
  })

  it('does not accept an edited segment beyond the source duration', async () => {
    const wrapper = mount(CandidateReviewPanel, {
      props: {
        candidates: [{ ...candidates[1], parameters: { ...candidates[1].parameters } }],
        warnings: [],
        maxSegmentEnd: 60,
      },
    })

    await wrapper.findAll('.segment-editor input')[1]!.setValue(61)

    expect(wrapper.get('.primary-action').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('不超过视频长度')
  })
})
