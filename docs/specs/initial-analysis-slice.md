# Initial action-analysis slice

Status: approved for implementation
Branch: `foundation/initial-analysis-slice`

## Outcome

Create the Web-first foundation for 哈基米练臂力动 and prove one complete
user-controlled action-analysis flow:

1. choose a backend-approved video source;
2. trigger analysis at the current playback time;
3. run ASR and visual understanding concurrently on a bounded window;
4. fuse reviewable action candidates;
5. let the user correct, select, and add candidates to one autosaved draft.

Training playback, calorie estimation, Pet, plan library/history, completion
poster, and sharing remain product requirements for later slices. They are not
part of this implementation.

## Engineering contract

- Vue 3 + TypeScript in `apps/web`; FastAPI + Pydantic in
  `services/analysis-api`.
- pnpm workspace, Node 24, pnpm 11, Python 3.12, uv; no Turbo or Nx.
- Project-managed FFmpeg executable; no machine-PATH dependency.
- MIT license under `Hachimi Contributors`. The previous GPL project is
  behaviour reference only; no code or unverified asset reuse.
- The runtime accepts only configured `source_id` values. Clients cannot submit
  arbitrary paths or URLs.
- Analysis runs are in memory, have a 180-second safety timeout, and retain
  terminal results for ten minutes. Cancellation discards late results.
- Default analysis covers 15 seconds before and 20 after the trigger. One empty
  evidence result may expand to 30 before and 40 after, clipped to the source.
- ASR and visual branches run concurrently. One reliable branch may produce a
  partial result with a user-safe warning; dual provider success with no
  evidence produces an empty result; system errors remain failures.
- Runtime test adapters are rejected outside `APP_ENV=test`.
- Every local run uses an isolated temporary directory. Ark files are deleted
  in `finally`, including schema failures.

## Public HTTP contract

- `GET /api/v1/sources`
- `GET /api/v1/sources/{source_id}/media` with HTTP Range support
- `POST /api/v1/analysis-runs`
- `GET /api/v1/analysis-runs/{run_id}/events` over SSE
- `GET /api/v1/analysis-runs/{run_id}`
- `DELETE /api/v1/analysis-runs/{run_id}`

Candidates expose an action name, source, absolute segment, sets, reps or
duration, rest, evidence spans, and confirmation need. They never expose model
confidence, internal logs, weight, or raw provider output. Missing parameters
remain `null`.

## Draft contract

- IndexedDB contains one `current` draft, autosaved with a 300ms debounce.
- Draft items embed source and parameter snapshots, so one draft can combine
  different videos and source-less manual actions.
- Users can reorder, duplicate, delete, and edit every training parameter.
- Adding a candidate applies rule defaults only when missing: 3 sets, 10 reps
  or 30 seconds, and 60 seconds rest. Weight stays blank and can only be user
  sourced. Unknown action mode requires an explicit user choice.
- Parameter values use `SourcedValue<T>` with `video | rule | user` provenance.

## Interaction and visual acceptance

- Primary viewport is 390×844; desktop keeps a centred vertical video stage.
- Analysis pauses video, names real stages, shows no fabricated percentage,
  and restores the prior playback state after cancellation.
- Candidate review supports segment preview, name/time correction,
  multi-selection, and list-order insertion.
- The design uses a dark sports-broadcast language with cyan/coral accents,
  local fonts, reduced-motion support, and no copied Douyin or GPL UI.
- Copy uses natural actions such as “继续找动作” and “返回视频”, never
  “返回信息流”.

## Security and verification

- Real keys exist only in ignored `.env.local`; `.env.example` has empty values.
  The browser bundle must contain no provider key.
- CI uses a synthetic video and deterministic test provider, never real cloud
  credentials.
- Required automated coverage includes allowlisting, Range media, window/time
  conversion, partial/empty/error outcomes, timeout/cancellation/cleanup,
  provider schema failures, SSE flow, late-result rejection, candidate
  correction, provenance defaults, multi-video/manual drafts, and refresh
  restoration.
- Before delivery, run lint, strict type checks, unit/integration/E2E, build,
  mobile/desktop visual QA, and the local real-cloud smoke at roughly 45s.
- Real-cloud acceptance requires both ASR and Ark evidence, a candidate
  semantically equivalent to Drag Curl/拖拽弯举 intersecting 41–51s, no weight,
  and confirmed local/Ark cleanup. Provider entitlement failures are reported
  as configuration blockers and are never replaced by mocks.
