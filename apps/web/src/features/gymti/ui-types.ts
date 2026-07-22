export interface GymtiOptionViewModel {
  id: string
  label: string
  detail?: string
}

export interface GymtiQuestionViewModel {
  id: string
  prompt: string
  context?: string
  options: GymtiOptionViewModel[]
}
