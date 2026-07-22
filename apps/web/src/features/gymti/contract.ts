import { COACH_STYLE_IDS, type CoachStyleId } from '@/domain/coach'
import { GYMTI_TYPES, type GymtiType } from '@/domain/gymti'

import rawQuestionnaire from '../../../../../contracts/gymti-questionnaire.v1.json'

import type {
  GymtiQuestion,
  GymtiQuestionOption,
  QuestionnaireContract,
  QuestionnaireRules,
} from './types'

type UnknownRecord = Record<string, unknown>

const isRecord = (value: unknown): value is UnknownRecord => (
  typeof value === 'object' && value !== null && !Array.isArray(value)
)

const isStringArray = (value: unknown): value is string[] => (
  Array.isArray(value) && value.every((item) => typeof item === 'string')
)

const isNonEmptyString = (value: unknown): value is string => (
  typeof value === 'string' && value.length > 0
)

const isScoreMap = (value: unknown, validIds: readonly string[]): value is Record<string, number> => (
  isRecord(value)
  && Object.entries(value).every(([id, score]) => (
    validIds.includes(id)
    && typeof score === 'number'
    && Number.isSafeInteger(score)
    && score !== 0
  ))
)

const addError = (errors: string[], condition: boolean, message: string): void => {
  if (!condition) errors.push(message)
}

const validateOption = (
  option: unknown,
  questionId: string,
  phase: string,
  knownReasonCodes: readonly string[],
  seenOptionIds: Set<string>,
  errors: string[],
): void => {
  if (!isRecord(option)) {
    errors.push(`${questionId}: option must be an object`)
    return
  }

  const optionId = option.id
  addError(errors, isNonEmptyString(optionId), `${questionId}: option id must be a non-empty string`)
  if (isNonEmptyString(optionId)) {
    addError(errors, !seenOptionIds.has(optionId), `${questionId}: duplicate option id ${optionId}`)
    seenOptionIds.add(optionId)
  }
  addError(errors, isNonEmptyString(option.label), `${questionId}: option label must be a non-empty string`)
  addError(errors, isStringArray(option.semanticTags), `${questionId}: option semanticTags must be strings`)
  addError(errors, isScoreMap(option.gymtiScores, GYMTI_TYPES), `${questionId}: option gymtiScores are invalid`)
  addError(errors, isScoreMap(option.coachStyleScores, COACH_STYLE_IDS), `${questionId}: option coachStyleScores are invalid`)
  addError(errors, isStringArray(option.hardBans) && option.hardBans.every((id) => COACH_STYLE_IDS.includes(id as CoachStyleId)), `${questionId}: option hardBans are invalid`)
  addError(errors, isStringArray(option.reasonCodes) && option.reasonCodes.every((id) => knownReasonCodes.includes(id)), `${questionId}: option reasonCodes are invalid`)
  addError(errors, typeof option.isNoMatch === 'boolean', `${questionId}: option isNoMatch must be boolean`)

  if (option.isNoMatch === true) {
    addError(errors, phase !== 'terminal', `${questionId}: terminal options cannot be no-match`)
    addError(errors, isRecord(option.gymtiScores) && Object.keys(option.gymtiScores).length === 0, `${questionId}: no-match cannot score a GYMTI type`)
    addError(errors, isRecord(option.coachStyleScores) && Object.keys(option.coachStyleScores).length === 0, `${questionId}: no-match cannot score a coach style`)
    addError(errors, Array.isArray(option.hardBans) && option.hardBans.length === 0, `${questionId}: no-match cannot hard-ban a coach style`)
    addError(errors, Array.isArray(option.reasonCodes) && option.reasonCodes.length === 0, `${questionId}: no-match cannot create a reason code`)
  }
}

const validateQuestion = (
  question: unknown,
  expectedPhase: 'foundation' | 'adaptive' | 'terminal',
  knownReasonCodes: readonly string[],
  seenQuestionIds: Set<string>,
  seenOptionIds: Set<string>,
  errors: string[],
): void => {
  if (!isRecord(question)) {
    errors.push(`${expectedPhase}: question must be an object`)
    return
  }

  const questionId = question.id
  addError(errors, isNonEmptyString(questionId), `${expectedPhase}: question id must be a non-empty string`)
  if (isNonEmptyString(questionId)) {
    addError(errors, !seenQuestionIds.has(questionId), `${expectedPhase}: duplicate question id ${questionId}`)
    seenQuestionIds.add(questionId)
  }
  const label = isNonEmptyString(questionId) ? questionId : expectedPhase
  addError(errors, question.phase === expectedPhase, `${label}: phase must be ${expectedPhase}`)
  addError(errors, isNonEmptyString(question.prompt), `${label}: prompt must be a non-empty string`)
  addError(errors, isStringArray(question.semanticTags), `${label}: semanticTags must be strings`)
  addError(errors, isRecord(question.probeTargets), `${label}: probeTargets must be an object`)
  if (isRecord(question.probeTargets)) {
    addError(errors, isStringArray(question.probeTargets.gymtiTypeIds) && question.probeTargets.gymtiTypeIds.every((id) => GYMTI_TYPES.includes(id as GymtiType)), `${label}: probeTargets.gymtiTypeIds are invalid`)
    addError(errors, isStringArray(question.probeTargets.coachStyleIds) && question.probeTargets.coachStyleIds.every((id) => COACH_STYLE_IDS.includes(id as CoachStyleId)), `${label}: probeTargets.coachStyleIds are invalid`)
  }
  addError(errors, Array.isArray(question.options) && question.options.length > 0, `${label}: options must be non-empty`)
  if (Array.isArray(question.options)) {
    for (const option of question.options) {
      validateOption(option, label, expectedPhase, knownReasonCodes, seenOptionIds, errors)
    }
  }
}

const sameIds = (actual: readonly string[], expected: readonly string[]): boolean => (
  actual.length === expected.length && actual.every((id, index) => id === expected[index])
)

const validateRules = (rules: unknown, allQuestionIds: readonly string[], errors: string[]): void => {
  if (!isRecord(rules)) {
    errors.push('rules must be an object')
    return
  }

  const questionCount = rules.questionCount
  addError(errors, isRecord(questionCount), 'rules.questionCount must be an object')
  if (isRecord(questionCount)) {
    addError(errors, questionCount.minimum === 5 && questionCount.maximum === 8, 'rules.questionCount must be 5 through 8')
  }

  const foundationIds = rules.foundationQuestionIds
  const adaptiveIds = rules.adaptiveQuestionIds
  addError(errors, isStringArray(foundationIds) && foundationIds.length > 0, 'rules.foundationQuestionIds must be non-empty strings')
  addError(errors, isStringArray(adaptiveIds) && adaptiveIds.length > 0, 'rules.adaptiveQuestionIds must be non-empty strings')
  if (isStringArray(foundationIds) && isStringArray(adaptiveIds)) {
    addError(errors, new Set([...foundationIds, ...adaptiveIds]).size === foundationIds.length + adaptiveIds.length, 'rules question ids cannot repeat')
    addError(errors, [...foundationIds, ...adaptiveIds].every((id) => allQuestionIds.includes(id)), 'rules question ids must refer to questions')
  }

  const earlyCompletion = rules.earlyCompletion
  addError(errors, isRecord(earlyCompletion), 'rules.earlyCompletion must be an object')
  if (isRecord(earlyCompletion)) {
    const afterAnswerCounts = earlyCompletion.afterAnswerCounts
    addError(errors, Array.isArray(afterAnswerCounts) && afterAnswerCounts.every((count) => typeof count === 'number') && sameIds(afterAnswerCounts.map(String), ['5', '6', '7']), 'rules.earlyCompletion.afterAnswerCounts must be 5, 6, 7')
    addError(errors, earlyCompletion.minimumWinnerScore === 4, 'rules.earlyCompletion.minimumWinnerScore must be 4')
    addError(errors, earlyCompletion.minimumLead === 3, 'rules.earlyCompletion.minimumLead must be 3')
    addError(errors, earlyCompletion.maximumNoMatchRatioExclusive === 0.5, 'rules.earlyCompletion.maximumNoMatchRatioExclusive must be 0.5')
  }

  const terminal = rules.terminal
  addError(errors, isRecord(terminal), 'rules.terminal must be an object')
  if (isRecord(terminal)) {
    addError(errors, terminal.answerCount === 8, 'rules.terminal.answerCount must be 8')
    addError(errors, typeof terminal.scoreBoost === 'number' && terminal.scoreBoost > 0, 'rules.terminal.scoreBoost must be positive')
  }
}

const scoreFromOption = (option: unknown, channel: 'gymtiScores' | 'coachStyleScores', id: string): number => (
  isRecord(option)
  && isRecord(option[channel])
  && typeof option[channel][id] === 'number'
    ? option[channel][id]
    : 0
)

const maximumPriorGap = (
  questions: readonly unknown[],
  ids: readonly string[],
  channel: 'gymtiScores' | 'coachStyleScores',
  maximumPriorAnswers: number,
): number => {
  const options = questions.flatMap((question) => (
    isRecord(question) && Array.isArray(question.options) ? question.options : []
  ))
  if (options.length === 0) return Number.POSITIVE_INFINITY

  return Math.max(0, ...ids.flatMap((targetId) => ids.map((competitorId) => {
    const minimumTarget = Math.min(...options.map((option) => scoreFromOption(option, channel, targetId)))
    const maximumCompetitor = Math.max(...options.map((option) => scoreFromOption(option, channel, competitorId)))
    return maximumPriorAnswers * (maximumCompetitor - minimumTarget)
  })))
}

const boostedIds = (
  option: unknown,
  channel: 'gymtiScores' | 'coachStyleScores',
  scoreBoost: number,
): string[] => (
  isRecord(option) && isRecord(option[channel])
    ? Object.entries(option[channel])
      .filter(([, score]) => typeof score === 'number' && score >= scoreBoost)
      .map(([id]) => id)
    : []
)

const validateTerminalGuarantees = (
  questions: readonly unknown[],
  terminalBank: readonly unknown[],
  scoreBoost: number,
  errors: string[],
): void => {
  const maximumPriorAnswers = 7
  const gymtiGap = maximumPriorGap(questions, GYMTI_TYPES, 'gymtiScores', maximumPriorAnswers)
  const coachStyleGap = maximumPriorGap(questions, COACH_STYLE_IDS, 'coachStyleScores', maximumPriorAnswers)
  addError(errors, scoreBoost > gymtiGap && scoreBoost > coachStyleGap, 'terminal scoreBoost must dominate every legal seven-answer score gap')

  for (const question of terminalBank) {
    if (!isRecord(question) || !isNonEmptyString(question.id) || !Array.isArray(question.options)) continue
    const targetStyles = new Set<string>()
    for (const option of question.options) {
      const optionId = isRecord(option) && isNonEmptyString(option.id) ? option.id : '<invalid-option>'
      const gymtiTargets = boostedIds(option, 'gymtiScores', scoreBoost)
      const coachStyleTargets = boostedIds(option, 'coachStyleScores', scoreBoost)
      addError(errors, gymtiTargets.length === 1, `${question.id}/${optionId}: terminal option must boost exactly one GYMTI type`)
      addError(errors, coachStyleTargets.length === 1, `${question.id}/${optionId}: terminal option must boost exactly one coach style`)
      if (coachStyleTargets.length === 1) targetStyles.add(coachStyleTargets[0])
    }
    addError(errors, targetStyles.size === 1, `${question.id}: terminal options must share one target coach style`)
  }
}

export function validateQuestionnaireContract(value: unknown): string[] {
  const errors: string[] = []
  if (!isRecord(value)) return ['contract must be an object']

  addError(errors, value.version === 'gymti-questionnaire.v1', 'version must be gymti-questionnaire.v1')
  addError(errors, Array.isArray(value.gymtiTypes), 'gymtiTypes must be an array')
  const gymtiTypes = Array.isArray(value.gymtiTypes) ? value.gymtiTypes : []
  const gymtiIds = gymtiTypes.flatMap((type) => isRecord(type) && isNonEmptyString(type.id) && isNonEmptyString(type.label) ? [type.id] : [])
  addError(errors, gymtiIds.length === gymtiTypes.length && sameIds(gymtiIds, GYMTI_TYPES), 'gymtiTypes must define the stable GYMTI ids in order')

  addError(errors, isStringArray(value.coachStyleIds) && sameIds(value.coachStyleIds, COACH_STYLE_IDS), 'coachStyleIds must define the stable coach style ids in order')
  addError(errors, Array.isArray(value.reasonCodes), 'reasonCodes must be an array')
  const reasonCodes = Array.isArray(value.reasonCodes) ? value.reasonCodes.flatMap((reason) => isRecord(reason) && isNonEmptyString(reason.id) ? [reason.id] : []) : []
  addError(errors, new Set(reasonCodes).size === reasonCodes.length && reasonCodes.length > 0, 'reasonCodes must have unique ids')

  const seenQuestionIds = new Set<string>()
  const seenOptionIds = new Set<string>()
  const questions = Array.isArray(value.questions) ? value.questions : []
  const terminalBank = Array.isArray(value.terminalBank) ? value.terminalBank : []
  addError(errors, Array.isArray(value.questions) && questions.length > 0, 'questions must be non-empty')
  addError(errors, Array.isArray(value.terminalBank) && terminalBank.length > 0, 'terminalBank must be non-empty')
  for (const question of questions) {
    const phase = isRecord(question) ? question.phase : undefined
    validateQuestion(question, phase === 'foundation' ? 'foundation' : 'adaptive', reasonCodes, seenQuestionIds, seenOptionIds, errors)
  }
  for (const question of terminalBank) {
    validateQuestion(question, 'terminal', reasonCodes, seenQuestionIds, seenOptionIds, errors)
  }

  const globallyHardBannedStyles = new Set(questions.flatMap((question) => (
    isRecord(question) && Array.isArray(question.options)
      ? question.options.flatMap((option) => (
        isRecord(option) && isStringArray(option.hardBans) ? option.hardBans : []
      ))
      : []
  )))
  addError(
    errors,
    COACH_STYLE_IDS.some((styleId) => !globallyHardBannedStyles.has(styleId)),
    'core hard bans must leave at least one coach style terminal route',
  )

  const allQuestionIds = [...seenQuestionIds]
  validateRules(value.rules, allQuestionIds, errors)

  if (isRecord(value.rules) && isRecord(value.rules.terminal) && typeof value.rules.terminal.scoreBoost === 'number') {
    const terminalScoreBoost = value.rules.terminal.scoreBoost
    validateTerminalGuarantees(questions, terminalBank, terminalScoreBoost, errors)
    const terminalStyleIds = terminalBank.flatMap((question) => (
      isRecord(question) && Array.isArray(question.options)
        ? question.options.flatMap((option) => (
          isRecord(option) && isRecord(option.coachStyleScores)
            ? Object.entries(option.coachStyleScores)
              .filter(([, score]) => typeof score === 'number' && score >= terminalScoreBoost)
              .map(([styleId]) => styleId)
            : []
        ))
        : []
    ))
    addError(errors, COACH_STYLE_IDS.every((styleId) => terminalStyleIds.includes(styleId)), 'terminalBank must provide a boosted terminal route for every coach style')
  }

  return errors
}

export function parseQuestionnaireContract(value: unknown): QuestionnaireContract {
  const errors = validateQuestionnaireContract(value)
  if (errors.length > 0) {
    throw new Error(`Invalid GYMTI questionnaire contract: ${errors.join('; ')}`)
  }
  return value as QuestionnaireContract
}

export const GYMTI_QUESTIONNAIRE = parseQuestionnaireContract(rawQuestionnaire)

export function allQuestionnaireQuestions(contract: QuestionnaireContract): readonly GymtiQuestion[] {
  return [...contract.questions, ...contract.terminalBank]
}

export function questionById(
  contract: QuestionnaireContract,
  questionId: string,
): GymtiQuestion | null {
  return allQuestionnaireQuestions(contract).find((question) => question.id === questionId) ?? null
}

export function optionById(
  question: GymtiQuestion,
  optionId: string,
): GymtiQuestionOption | null {
  return question.options.find((option) => option.id === optionId) ?? null
}

export function contractRules(contract: QuestionnaireContract): QuestionnaireRules {
  return contract.rules
}
