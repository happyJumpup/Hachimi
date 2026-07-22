import { COACH_STYLE_IDS, type CoachStyleId } from '@/domain/coach'
import { GYMTI_TYPES, type GymtiType } from '@/domain/gymti'

import {
  optionById,
  questionById,
} from './contract'
import type {
  GymtiAnswer,
  GymtiEvaluation,
  GymtiFormalResult,
  GymtiQuestion,
  GymtiQuestionOption,
  GymtiScoreState,
  GymtiTypeId,
  QuestionnaireContract,
  ReasonCode,
} from './types'

export class GymtiAnswerValidationError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'GymtiAnswerValidationError'
  }
}

const zeroGymtiScores = (): Record<GymtiTypeId, number> => Object.fromEntries(
  GYMTI_TYPES.map((id) => [id, 0]),
) as Record<GymtiTypeId, number>

const zeroCoachStyleScores = (): Record<CoachStyleId, number> => Object.fromEntries(
  COACH_STYLE_IDS.map((id) => [id, 0]),
) as Record<CoachStyleId, number>

interface UniqueWinner<T extends string> {
  id: T
  score: number
  runnerUpScore: number
  lead: number
}

const uniqueWinner = <T extends string>(
  ids: readonly T[],
  scores: Readonly<Record<T, number>>,
  excludedIds: ReadonlySet<T> = new Set<T>(),
): UniqueWinner<T> | null => {
  const eligibleIds = ids.filter((id) => !excludedIds.has(id))
  if (eligibleIds.length === 0) return null

  const winnerScore = Math.max(...eligibleIds.map((id) => scores[id]))
  const winnerIds = eligibleIds.filter((id) => scores[id] === winnerScore)
  if (winnerIds.length !== 1) return null

  const winnerId = winnerIds[0]
  const remainingScores = eligibleIds
    .filter((id) => id !== winnerId)
    .map((id) => scores[id])
  const runnerUpScore = remainingScores.length === 0 ? Number.NEGATIVE_INFINITY : Math.max(...remainingScores)

  return {
    id: winnerId,
    score: winnerScore,
    runnerUpScore,
    lead: winnerScore - runnerUpScore,
  }
}

const secondaryGymtiType = (
  scores: Readonly<Record<GymtiType, number>>,
  primary: UniqueWinner<GymtiType>,
): GymtiType | null => {
  const runnerUpIds = GYMTI_TYPES.filter((id) => (
    id !== primary.id && scores[id] === primary.runnerUpScore
  ))
  if (runnerUpIds.length !== 1) return null
  if (primary.runnerUpScore <= 0 || primary.lead > 1) return null
  return runnerUpIds[0]
}

const positiveReasonCodes = (
  contract: QuestionnaireContract,
  scoreState: GymtiScoreState,
  result: Pick<GymtiFormalResult, 'gymtiType' | 'recommendedCoachStyleId'>,
): string[] => {
  const weights = new Map<ReasonCode, { score: number; answerIndex: number }>()

  scoreState.answers.forEach((answer, answerIndex) => {
    const question = questionById(contract, answer.questionId)
    const option = question === null ? null : optionById(question, answer.optionId)
    if (option === null) return

    const weight = Math.max(
      option.gymtiScores[result.gymtiType] ?? 0,
      option.coachStyleScores[result.recommendedCoachStyleId] ?? 0,
    )
    if (weight <= 0) return

    for (const reasonCode of option.reasonCodes) {
      const current = weights.get(reasonCode)
      if (current === undefined || weight > current.score) {
        weights.set(reasonCode, { score: weight, answerIndex })
      }
    }
  })

  return [...weights.entries()]
    .sort(([leftCode, left], [rightCode, right]) => (
      right.score - left.score
      || left.answerIndex - right.answerIndex
      || leftCode.localeCompare(rightCode)
    ))
    .slice(0, 3)
    .map(([reasonCode]) => reasonCode)
}

const addScores = <T extends string>(
  totals: Record<T, number>,
  increments: Readonly<Partial<Record<T, number>>>,
): void => {
  for (const [id, value] of Object.entries(increments) as [T, number][]) {
    totals[id] += value
  }
}

const requireOption = (
  contract: QuestionnaireContract,
  answer: GymtiAnswer,
): GymtiQuestionOption => {
  const question = questionById(contract, answer.questionId)
  if (question === null) {
    throw new GymtiAnswerValidationError(`Unknown GYMTI question: ${answer.questionId}`)
  }
  const option = optionById(question, answer.optionId)
  if (option === null) {
    throw new GymtiAnswerValidationError(`Unknown GYMTI option ${answer.optionId} for ${answer.questionId}`)
  }
  return option
}

export function scoreGymtiAnswers(
  contract: QuestionnaireContract,
  answers: readonly GymtiAnswer[],
): GymtiScoreState {
  const gymtiScores = zeroGymtiScores()
  const coachStyleScores = zeroCoachStyleScores()
  const excludedIds = new Set<CoachStyleId>()
  const seenQuestionIds = new Set<string>()
  let noMatchCount = 0

  for (const answer of answers) {
    if (seenQuestionIds.has(answer.questionId)) {
      throw new GymtiAnswerValidationError(`GYMTI question answered twice: ${answer.questionId}`)
    }
    seenQuestionIds.add(answer.questionId)

    const option = requireOption(contract, answer)
    addScores(gymtiScores, option.gymtiScores)
    addScores(coachStyleScores, option.coachStyleScores)
    for (const styleId of option.hardBans) excludedIds.add(styleId)
    if (option.isNoMatch) noMatchCount += 1
  }

  return {
    answers: answers.map((answer) => ({ ...answer })),
    gymtiScores,
    coachStyleScores,
    excludedCoachStyleIds: COACH_STYLE_IDS.filter((styleId) => excludedIds.has(styleId)),
    noMatchCount,
  }
}

export function deriveFormalResult(
  contract: QuestionnaireContract,
  scoreState: GymtiScoreState,
): GymtiFormalResult | null {
  const gymtiWinner = uniqueWinner(GYMTI_TYPES, scoreState.gymtiScores)
  const excludedIds = new Set(scoreState.excludedCoachStyleIds)
  const coachStyleWinner = uniqueWinner(COACH_STYLE_IDS, scoreState.coachStyleScores, excludedIds)
  if (gymtiWinner === null || coachStyleWinner === null) return null

  const bareResult = {
    gymtiType: gymtiWinner.id,
    secondaryGymtiType: secondaryGymtiType(scoreState.gymtiScores, gymtiWinner),
    recommendedCoachStyleId: coachStyleWinner.id,
  }

  return {
    ...bareResult,
    reasonCodes: positiveReasonCodes(contract, scoreState, bareResult),
    excludedCoachStyleIds: [...scoreState.excludedCoachStyleIds],
  }
}

export function meetsEarlyCompletion(
  contract: QuestionnaireContract,
  scoreState: GymtiScoreState,
): boolean {
  const { earlyCompletion } = contract.rules
  if (!earlyCompletion.afterAnswerCounts.includes(scoreState.answers.length)) return false
  if (scoreState.answers.some((answer) => questionById(contract, answer.questionId)?.phase === 'terminal')) return false
  if (scoreState.noMatchCount >= scoreState.answers.length * earlyCompletion.maximumNoMatchRatioExclusive) return false

  const gymtiWinner = uniqueWinner(GYMTI_TYPES, scoreState.gymtiScores)
  const coachStyleWinner = uniqueWinner(
    COACH_STYLE_IDS,
    scoreState.coachStyleScores,
    new Set(scoreState.excludedCoachStyleIds),
  )
  if (gymtiWinner === null || coachStyleWinner === null) return false

  return (
    gymtiWinner.score >= earlyCompletion.minimumWinnerScore
    && gymtiWinner.lead >= earlyCompletion.minimumLead
    && coachStyleWinner.score >= earlyCompletion.minimumWinnerScore
    && coachStyleWinner.lead >= earlyCompletion.minimumLead
  )
}

export function evaluateGymtiAnswers(
  contract: QuestionnaireContract,
  answers: readonly GymtiAnswer[],
): GymtiEvaluation {
  const scoreState = scoreGymtiAnswers(contract, answers)
  const rankedResult = deriveFormalResult(contract, scoreState)
  const earlyCompletion = meetsEarlyCompletion(contract, scoreState)
  const lastAnswer = scoreState.answers.at(-1)
  const terminalCompletion = (
    scoreState.answers.length === contract.rules.terminal.answerCount
    && lastAnswer !== undefined
    && questionById(contract, lastAnswer.questionId)?.phase === 'terminal'
  )

  return {
    scoreState,
    rankedResult,
    formalResult: earlyCompletion || terminalCompletion ? rankedResult : null,
    earlyCompletion,
  }
}

const containsTerminalAnswer = (
  contract: QuestionnaireContract,
  answers: readonly GymtiAnswer[],
): boolean => answers.some((answer) => questionById(contract, answer.questionId)?.phase === 'terminal')

const tiedHighestIds = <T extends string>(
  ids: readonly T[],
  scores: Readonly<Record<T, number>>,
  excludedIds: ReadonlySet<T> = new Set<T>(),
): Set<T> => {
  const eligibleIds = ids.filter((id) => !excludedIds.has(id))
  if (eligibleIds.length < 2) return new Set<T>()
  const highestScore = Math.max(...eligibleIds.map((id) => scores[id]))
  const tiedIds = eligibleIds.filter((id) => scores[id] === highestScore)
  return tiedIds.length > 1 ? new Set(tiedIds) : new Set<T>()
}

const intersectionSize = <T extends string>(
  targets: readonly T[],
  soughtIds: ReadonlySet<T>,
): number => targets.reduce((count, id) => count + Number(soughtIds.has(id)), 0)

const adaptiveQuestionPriority = (
  question: GymtiQuestion,
  tiedGymtiIds: ReadonlySet<GymtiTypeId>,
  tiedCoachStyleIds: ReadonlySet<CoachStyleId>,
  missingGymtiIds: ReadonlySet<GymtiTypeId>,
  missingCoachStyleIds: ReadonlySet<CoachStyleId>,
): { tieCoverage: number; missingCoverage: number } => ({
  tieCoverage: (
    intersectionSize(question.probeTargets.gymtiTypeIds, tiedGymtiIds)
    + intersectionSize(question.probeTargets.coachStyleIds, tiedCoachStyleIds)
  ),
  missingCoverage: (
    intersectionSize(question.probeTargets.gymtiTypeIds, missingGymtiIds)
    + intersectionSize(question.probeTargets.coachStyleIds, missingCoachStyleIds)
  ),
})

const prioritizeAdaptiveQuestions = (
  scoreState: GymtiScoreState,
  questions: readonly GymtiQuestion[],
): GymtiQuestion[] => {
  if (questions.length < 2) return [...questions]

  const excludedStyleIds = new Set(scoreState.excludedCoachStyleIds)
  const tiedGymtiIds = tiedHighestIds(GYMTI_TYPES, scoreState.gymtiScores)
  const tiedCoachStyleIds = tiedHighestIds(
    COACH_STYLE_IDS,
    scoreState.coachStyleScores,
    excludedStyleIds,
  )
  const missingGymtiIds = new Set(
    GYMTI_TYPES.filter((id) => scoreState.gymtiScores[id] <= 0),
  )
  const missingCoachStyleIds = new Set(
    COACH_STYLE_IDS.filter((id) => (
      !excludedStyleIds.has(id) && scoreState.coachStyleScores[id] <= 0
    )),
  )
  const priorities = new Map(questions.map((question) => [
    question.id,
    adaptiveQuestionPriority(
      question,
      tiedGymtiIds,
      tiedCoachStyleIds,
      missingGymtiIds,
      missingCoachStyleIds,
    ),
  ]))

  const maximumTieCoverage = Math.max(
    ...questions.map((question) => priorities.get(question.id)?.tieCoverage ?? 0),
  )
  let narrowed = maximumTieCoverage === 0
    ? [...questions]
    : questions.filter(
      (question) => priorities.get(question.id)?.tieCoverage === maximumTieCoverage,
    )
  const maximumMissingCoverage = Math.max(
    ...narrowed.map((question) => priorities.get(question.id)?.missingCoverage ?? 0),
  )
  if (maximumMissingCoverage > 0) {
    narrowed = narrowed.filter(
      (question) => priorities.get(question.id)?.missingCoverage === maximumMissingCoverage,
    )
  }

  return narrowed
}

const terminalTargetStyleIds = (
  contract: QuestionnaireContract,
  questionId: string,
): CoachStyleId[] => {
  const question = questionById(contract, questionId)
  if (question === null || question.phase !== 'terminal') return []
  const targetIds = new Set<CoachStyleId>()
  for (const option of question.options) {
    for (const [styleId, score] of Object.entries(option.coachStyleScores) as [CoachStyleId, number][]) {
      if (score >= contract.rules.terminal.scoreBoost) targetIds.add(styleId)
    }
  }
  return COACH_STYLE_IDS.filter((styleId) => targetIds.has(styleId))
}

export function terminalQuestionGuaranteeViolations(
  contract: QuestionnaireContract,
  answers: readonly GymtiAnswer[],
  terminalQuestionId: string,
): string[] {
  const violations: string[] = []
  const question = questionById(contract, terminalQuestionId)
  if (question === null || question.phase !== 'terminal') {
    return [`${terminalQuestionId} is not a terminal question`]
  }
  if (answers.length !== contract.rules.terminal.answerCount - 1) {
    return [`${terminalQuestionId} requires exactly ${contract.rules.terminal.answerCount - 1} prior answers`]
  }
  if (containsTerminalAnswer(contract, answers)) {
    return [`${terminalQuestionId} cannot follow a terminal answer`]
  }

  const priorState = scoreGymtiAnswers(contract, answers)
  const excludedIds = new Set(priorState.excludedCoachStyleIds)
  const blockedTargetIds = terminalTargetStyleIds(contract, terminalQuestionId)
    .filter((styleId) => excludedIds.has(styleId))
  if (blockedTargetIds.length > 0) {
    return [`${terminalQuestionId} targets hard-banned styles: ${blockedTargetIds.join(', ')}`]
  }

  for (const option of question.options) {
    const evaluation = evaluateGymtiAnswers(contract, [
      ...answers,
      { questionId: question.id, optionId: option.id },
    ])
    if (evaluation.formalResult === null) {
      violations.push(`${terminalQuestionId}/${option.id} does not resolve a unique result`)
      continue
    }
    if (excludedIds.has(evaluation.formalResult.recommendedCoachStyleId)) {
      violations.push(`${terminalQuestionId}/${option.id} reintroduced a hard-banned style`)
    }
  }

  return violations
}

export function legalNextQuestionCandidates(
  contract: QuestionnaireContract,
  answers: readonly GymtiAnswer[],
) {
  const scoreState = scoreGymtiAnswers(contract, answers)
  const { rules } = contract
  if (answers.length >= rules.questionCount.maximum || containsTerminalAnswer(contract, answers)) return []

  const answeredQuestionIds = new Set(answers.map((answer) => answer.questionId))
  if (answers.length < rules.foundationQuestionIds.length) {
    const nextQuestionId = rules.foundationQuestionIds[answers.length]
    const nextQuestion = nextQuestionId === undefined ? null : questionById(contract, nextQuestionId)
    return nextQuestion === null || answeredQuestionIds.has(nextQuestion.id) ? [] : [nextQuestion]
  }

  if (answers.length >= rules.questionCount.minimum && meetsEarlyCompletion(contract, scoreState)) return []

  if (answers.length < rules.terminal.answerCount - 1) {
    const unansweredAdaptiveQuestions = rules.adaptiveQuestionIds
      .filter((questionId) => !answeredQuestionIds.has(questionId))
      .flatMap((questionId) => {
        const question = questionById(contract, questionId)
        return question === null || question.phase !== 'adaptive' ? [] : [question]
      })
    return prioritizeAdaptiveQuestions(scoreState, unansweredAdaptiveQuestions)
  }

  if (answers.length === rules.terminal.answerCount - 1) {
    return contract.terminalBank.filter((question) => (
      terminalQuestionGuaranteeViolations(contract, answers, question.id).length === 0
    ))
  }

  return []
}

export function deterministicLocalFallback(
  candidateQuestionIds: readonly string[],
): string | null {
  return [...new Set(candidateQuestionIds)][0] ?? null
}
