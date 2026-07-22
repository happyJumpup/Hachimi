export {
  allQuestionnaireQuestions,
  contractRules,
  GYMTI_QUESTIONNAIRE,
  optionById,
  parseQuestionnaireContract,
  questionById,
  validateQuestionnaireContract,
} from './contract'

export {
  deterministicLocalFallback,
  deriveFormalResult,
  evaluateGymtiAnswers,
  GymtiAnswerValidationError,
  legalNextQuestionCandidates,
  meetsEarlyCompletion,
  scoreGymtiAnswers,
  terminalQuestionGuaranteeViolations,
} from './engine'

export type {
  GymtiAnswer,
  GymtiEvaluation,
  GymtiFormalResult,
  GymtiQuestion,
  GymtiQuestionOption,
  GymtiScoreState,
  GymtiTypeId,
  QuestionnaireContract,
  QuestionnaireRules,
  ReasonCode,
} from './types'
