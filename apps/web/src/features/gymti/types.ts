import type { CoachStyleId } from '@/domain/coach'
import type {
  CompletedGymtiResult,
  GymtiAnswerRef,
  GymtiType,
} from '@/domain/gymti'

export type GymtiTypeId = GymtiType
export type GymtiAnswer = GymtiAnswerRef
export type GymtiFormalResult = CompletedGymtiResult
export type ReasonCode = string

export type ScoreMap<T extends string> = Partial<Record<T, number>>

export interface GymtiTypeDefinition {
  id: GymtiTypeId
  label: string
}

export interface ReasonCodeDefinition {
  id: ReasonCode
}

export interface GymtiProbeTargets {
  gymtiTypeIds: readonly GymtiTypeId[]
  coachStyleIds: readonly CoachStyleId[]
}

export interface GymtiQuestionOption {
  id: string
  label: string
  semanticTags: readonly string[]
  gymtiScores: ScoreMap<GymtiTypeId>
  coachStyleScores: ScoreMap<CoachStyleId>
  hardBans: readonly CoachStyleId[]
  reasonCodes: readonly ReasonCode[]
  isNoMatch: boolean
}

export interface GymtiQuestion {
  id: string
  phase: 'foundation' | 'adaptive' | 'terminal'
  prompt: string
  semanticTags: readonly string[]
  probeTargets: GymtiProbeTargets
  options: readonly GymtiQuestionOption[]
}

export interface QuestionnaireRules {
  questionCount: {
    minimum: number
    maximum: number
  }
  foundationQuestionIds: readonly string[]
  adaptiveQuestionIds: readonly string[]
  earlyCompletion: {
    afterAnswerCounts: readonly number[]
    minimumWinnerScore: number
    minimumLead: number
    maximumNoMatchRatioExclusive: number
  }
  terminal: {
    answerCount: number
    scoreBoost: number
  }
}

export interface QuestionnaireContract {
  version: string
  gymtiTypes: readonly GymtiTypeDefinition[]
  coachStyleIds: readonly CoachStyleId[]
  reasonCodes: readonly ReasonCodeDefinition[]
  rules: QuestionnaireRules
  questions: readonly GymtiQuestion[]
  terminalBank: readonly GymtiQuestion[]
}

export interface GymtiScoreState {
  answers: readonly GymtiAnswer[]
  gymtiScores: Readonly<Record<GymtiTypeId, number>>
  coachStyleScores: Readonly<Record<CoachStyleId, number>>
  excludedCoachStyleIds: readonly CoachStyleId[]
  noMatchCount: number
}

export interface GymtiEvaluation {
  scoreState: GymtiScoreState
  rankedResult: GymtiFormalResult | null
  formalResult: GymtiFormalResult | null
  earlyCompletion: boolean
}
