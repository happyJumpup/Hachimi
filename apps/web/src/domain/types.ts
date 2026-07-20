export type ActionMode = 'reps' | 'duration'
export type ValueSource = 'video' | 'rule' | 'user'

export interface Segment {
  start_seconds: number
  end_seconds: number
}

export interface EvidenceSpan extends Segment {
  type: 'speech' | 'visual'
}

export interface CandidateParameters {
  mode: ActionMode | null
  sets: number | null
  reps: number | null
  duration_seconds: number | null
  rest_seconds: number | null
}

export interface AnalysisCandidate {
  id: string
  name: string
  source_id: string
  segment: Segment | null
  parameters: CandidateParameters
  evidence: EvidenceSpan[]
  needs_confirmation: boolean
}

export interface SourceSummary {
  id: string
  title: string
  media_url: string
  duration_seconds: number
}

export interface AnalysisWarning {
  code: 'speech_unavailable' | 'visual_unavailable'
  message: string
}

export interface AnalysisError {
  code: 'configuration_error' | 'provider_error' | 'schema_error' | 'media_error' | 'timeout' | 'cancelled'
  message: string
  retryable: boolean
}

export interface AnalysisRun {
  id: string
  source_id: string
  trigger_seconds: number
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
  stage:
    | 'queued'
    | 'preparing_media'
    | 'analyzing_evidence'
    | 'expanding_window'
    | 'fusing_candidates'
    | 'completed'
    | 'failed'
    | 'cancelled'
  candidates: AnalysisCandidate[]
  warnings: AnalysisWarning[]
  empty_reason: 'no_evidence' | null
  error: AnalysisError | null
  created_at: string
  updated_at: string
}

export interface SourcedValue<T> {
  value: T | null
  source: ValueSource | null
}

export interface DraftSourceRef {
  sourceId: string
  title?: string
}

export interface DraftItem {
  id: string
  name: string
  sourceRef: DraftSourceRef | null
  segment: SourcedValue<Segment>
  mode: ActionMode
  sets: SourcedValue<number>
  reps: SourcedValue<number>
  durationSeconds: SourcedValue<number>
  restSeconds: SourcedValue<number>
  weightKg: SourcedValue<number>
}

export interface DraftPlan {
  id: 'current'
  name: string
  linkedPlanId: string | null
  items: DraftItem[]
  updatedAt: string
}

export interface DraftRepository {
  load(): Promise<DraftPlan | undefined>
  save(plan: DraftPlan): Promise<void>
}
