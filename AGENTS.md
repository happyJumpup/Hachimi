# Repository instructions

## Working principles

1. **Think before coding.** State material assumptions, inspect the relevant
   implementation and domain decisions, and surface ambiguity before it turns
   into code.
2. **Simplicity first.** Build the smallest complete vertical slice. Do not add
   speculative abstractions, queues, databases, or framework layers.
3. **Surgical changes.** Preserve existing work, keep changes inside the named
   scope, and avoid opportunistic rewrites.
4. **Goal-driven execution.** Define observable success, verify through public
   interfaces, and finish with proportional tests and a clean handoff.

## Before making changes

- Read `CONTEXT.md`, the ADRs that touch the change, and the relevant GitHub
  issue including its comments and labels.
- Use the domain terms exactly as defined in `CONTEXT.md`.
- If a requested change contradicts an ADR, call out the conflict instead of
  silently overriding it.

## Product and architecture boundaries

- The product is Web-first. A future Douyin adapter may supply controlled video
  sources, but arbitrary URL fetching and feed scraping are out of scope.
- The core object is a **训练动作**, not a complete video. A video action keeps
  its source and demonstration time range; a self-created action may have none.
- The runtime has one task-oriented 动作分析 Agent. Its speech, visual, and
  fusion Skills propose candidates; the user chooses and edits the plan.
- Analysis Runs are transient, cancellable, and never continue as background
  jobs. Do not introduce runtime mock fallbacks.
- Do not copy source code or components from the GPL-3.0 historical demo.
- Analysis starts only after an explicit user action and covers the complete
  controlled source up to 60 seconds. Legacy trigger metadata must not narrow
  scope, and page load, playback, or seeking must not auto-create a run.

## Security and data handling

- Never print, log, commit, expose to the frontend, or include secrets in test
  artifacts. Local credentials belong only in ignored `.env.local` files.
- Raw audio, video windows, frames, transcripts, prompts, and model responses
  are transient. Delete local and provider-side files on success, cancellation,
  and failure.
- Logs may contain run/source IDs, stages, timing, version identifiers, provider
  request IDs, and redacted error codes only.

## Verification

- Test behaviour at public seams: HTTP/SSE APIs, store actions, and visible
  user flows. Fake providers are allowed only in tests and must fail closed in
  other environments.
- Run focused tests while working and the complete `pnpm check` plus E2E suite
  before delivery. Real cloud smoke tests stay local and produce sanitized
  status evidence only.

## Agent skills

### Issue tracker

Issues and PRDs are tracked in GitHub Issues for `happyJumpup/Hachimi`. See
`docs/agents/issue-tracker.md`.

### Triage labels

Use the canonical labels `needs-triage`, `needs-info`, `ready-for-agent`,
`ready-for-human`, and `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

This is a single-context repository. Read the root `CONTEXT.md` and relevant
ADRs under `docs/adr/`. See `docs/agents/domain.md`.
