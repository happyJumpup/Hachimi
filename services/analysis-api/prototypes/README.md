# Long-video provider spikes

## Qwen3-VL direct-video spike

Question: can a single direct-video Qwen3-VL request turn the complete seven-minute
fitness video into a structured, time-addressable action list quickly enough to
replace the fixed contact-sheet path?

Command:

```powershell
pnpm spike:qwen-long-video
```

The command reads an ignored `.env.local` and uploads the source to DashScope's
model-bound temporary OSS. DashScope retains that temporary object for up to
48 hours and does not expose a delete API; the spike does not persist the raw
model response locally. This transport is benchmark-only and must not be used by
the production analysis API.

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

### Short-video confirmation

The same question was retested on 2026-07-21 with the existing 54.87-second,
13.4 MB controlled demo video at 2.0 FPS:

- Manual temporary-OSS transport disconnected during upload after 62.3 s.
- The official DashScope SDK local-file transport produced no terminal response
  within the three-minute stop line and was terminated.
- No candidate content was returned, so the expected action around 41–51 seconds
  could not be evaluated.

This decision was superseded by the diagnosed rerun below. The earlier failures
were caused by the local Vortex/Fake-IP upload path, not by Qwen inference.

### Diagnosed rerun and corrected decision

The Windows resolver mapped the DashScope OSS host to the Vortex Fake-IP range,
and large multipart or Base64 POST bodies were reset. Policy acquisition remained
healthy and reported a 1024 MB account/model upload limit. A curl SOCKS upload
probe established a working low-payload path and allowed the complete controlled
54.87-second video to reach Qwen3-VL.

Three complete-video inference runs succeeded:

| Input | Upload | Inference | Video tokens | Result |
| --- | ---: | ---: | ---: | --- |
| 160×284, 5 FPS, 116 KB | 13.16 s | 7.60 s | 6,167 | 5 actions; drag curl 40.9–49.5 s |
| 240×426, 6 FPS, 253 KB | 32.66 s | 6.43 s | 9,842 | 5 actions; drag curl 41–49 s |
| 240×426, 2 FPS, 253 KB | 29.29 s | 5.28 s | 3,302 | 5 actions; drag curl 41–49 s |

All three covered the full source, intersected the 41–51 second human annotation,
and produced no weight field. The stable action list contained normal curl,
light-weight curl, seated curl, cross-body curl, and drag curl.

Corrected decision: retain direct Qwen video understanding as a provider candidate.
At 2 FPS, model inference meets the 5–15 seconds per video-minute target on the
controlled short video. Production evaluation must place media on a provider-
reachable OSS/CDN or run from the deployment network; the Codex desktop Vortex
upload timing is not representative. Long-video acceptance still requires a real
deployment-network benchmark before choosing whole-video versus bounded chunks.
