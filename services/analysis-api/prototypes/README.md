# Long-video provider spikes

## Qwen3-VL direct-video spike

Question: can a single direct-video Qwen3-VL request turn the complete seven-minute
fitness video into a structured, time-addressable action list quickly enough to
replace the fixed contact-sheet path?

Command:

```powershell
pnpm spike:qwen-long-video
```

The command reads an ignored `.env.local` and uploads the source only to
DashScope's model-bound temporary OSS. It does not persist the video or raw model
response.

Observed on 2026-07-21 with `qwen3-vl-flash`:

- 1.0 FPS, non-streaming: provider disconnected without a response after 476.6 s.
- 0.5 FPS, SSE: no terminal result before the 1004 s outer safety timeout.
- Neither run returned a candidate list, so recognition quality and timestamp
  accuracy remain unverified.
- Both runs miss the product target of 5–15 seconds per minute of source video.

Decision: reject a single whole-video request as the synchronous user path. Keep
Qwen only as a possible provider behind a later bounded-chunk benchmark with
progressive results, overlap deduplication, and explicit upload/first-token/final
timings.
