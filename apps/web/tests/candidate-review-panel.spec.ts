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
    segment_role: 'unknown',
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
    segment_role: 'follow_along',
  },
]

describe('CandidateReviewPanel', () => {
  it('lets the user return to the video without adding a candidate', async () => {
    const wrapper = mount(CandidateReviewPanel, {
      props: { candidates, warnings: [] },
    })

    await wrapper.get('button[aria-label="返回视频"]').trigger('click')

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
    await cards[0]
      .findAll('.role-picker button')
      .find((button) => button.text() === '跟练执行')!
      .trigger('click')
    await cards[1].get<HTMLInputElement>('.candidate-select input').setValue(false)

    expect(addButton.attributes('disabled')).toBeUndefined()
    await addButton.trigger('click')

    const [[chosen, editedIds, editedRoleIds]] = wrapper.emitted<[
      AnalysisCandidate[],
      string[],
      string[],
    ]>('add')!
    expect(chosen).toHaveLength(1)
    expect(chosen[0]).toMatchObject({
      id: 'candidate-a',
      name: '单臂拖拽弯举',
      segment: { start_seconds: 42, end_seconds: 51 },
      parameters: { mode: 'reps' },
      segment_role: 'follow_along',
    })
    expect(editedIds).toEqual(['candidate-a'])
    expect(editedRoleIds).toEqual(['candidate-a'])
    expect(candidates[0].name).toBe('拖拽弯举')
  })

  it('requires a role choice for unknown segments and explains known teaching segments', async () => {
    const wrapper = mount(CandidateReviewPanel, {
      props: {
        candidates: [{
          ...candidates[1],
          segment_role: 'teaching_demo',
          parameters: { ...candidates[1].parameters },
        }],
        warnings: [],
      },
    })

    expect(wrapper.text()).toContain('教学演示')
    expect(wrapper.text()).toContain('片段只用于预览')

    await wrapper.setProps({
      candidates: [{
        ...candidates[1],
        id: 'unknown-role',
        segment_role: 'unknown',
        parameters: { ...candidates[1].parameters },
      }],
    })
    expect(wrapper.get('.primary-action').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('先确认片段用途')
  })

  it('keeps root candidate edits when a gap retry adds another candidate', async () => {
    const root = { ...candidates[1]!, parameters: { ...candidates[1]!.parameters } }
    const wrapper = mount(CandidateReviewPanel, {
      props: { candidates: [root], warnings: [] },
    })
    await wrapper.get('.action-name-input').setValue('我修改过的锤式弯举')
    await wrapper.findAll('.segment-editor input')[0]!.setValue(55)

    await wrapper.setProps({
      candidates: [
        root,
        {
          ...root,
          id: 'run-gap:candidate-1',
          name: '缺口新动作',
          segment: { start_seconds: 20, end_seconds: 30 },
        },
      ],
    })

    const cards = wrapper.findAll('.candidate-card')
    expect(cards).toHaveLength(2)
    expect(cards[0]!.get<HTMLInputElement>('.action-name-input').element.value).toBe('我修改过的锤式弯举')
    expect(cards[0]!.findAll<HTMLInputElement>('.segment-editor input')[0]!.element.value).toBe('55')
  })

  it('keeps a failed coverage gap visible and emits a retry for only that range', async () => {
    const gap = {
      start_seconds: 20,
      end_seconds: 30,
      reason: 'timeout' as const,
      retryable: true,
    }
    const wrapper = mount(CandidateReviewPanel, {
      props: {
        candidates: [candidates[1]!],
        warnings: [],
        coverageGaps: [gap],
        gapRetryError: '这段暂时没有重试成功，缺口已保留',
      },
    })

    const coverage = wrapper.get('.coverage-gaps')
    expect(coverage.text()).toContain('部分完成')
    expect(coverage.text()).toContain('0:20—0:30')
    expect(coverage.text()).toContain('处理超时')
    expect(coverage.text()).not.toContain('provider')
    expect(coverage.text()).toContain('缺口已保留')
    await coverage.get('button').trigger('click')
    expect(wrapper.emitted('retryGap')).toEqual([[gap]])
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
