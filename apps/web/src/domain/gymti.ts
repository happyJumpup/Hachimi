import type { CoachStyleId } from '@/domain/coach'

export const GYMTI_TYPES = ['IMNB', 'KCAL', 'HIDE', 'LIFE', 'CURV', 'BOOM', 'WINN'] as const

export type GymtiType = typeof GYMTI_TYPES[number]

export interface GymtiAnswerRef {
  questionId: string
  optionId: string
}

export interface CompletedGymtiResult {
  gymtiType: GymtiType
  secondaryGymtiType: GymtiType | null
  recommendedCoachStyleId: CoachStyleId
  reasonCodes: string[]
  excludedCoachStyleIds: CoachStyleId[]
}

export interface GymtiNarrativeSnapshot {
  text: string
  source: 'llm' | 'template'
  version: string
  model: string | null
  generatedAt: string
}

export interface GymtiAttempt {
  id: 'current'
  attemptId: string
  questionnaireVersion: string
  scoringVersion: string
  answers: GymtiAnswerRef[]
  currentQuestionId: string
  phase: 'questionnaire' | 'profile'
  originPath: string
  startedAt: string
  updatedAt: string
}

interface PersistedGymtiResultBase {
  id: 'current'
  resultId: string
  attemptId: string
  questionnaireVersion: string
  scoringVersion: string
  answers: GymtiAnswerRef[]
  result: CompletedGymtiResult
  narrative: GymtiNarrativeSnapshot | null
  createdAt: string
  updatedAt: string
}

export type PendingGymtiResult = PersistedGymtiResultBase

export interface CurrentGymtiResult extends PersistedGymtiResultBase {
  narrative: GymtiNarrativeSnapshot
  activatedAt: string
}
