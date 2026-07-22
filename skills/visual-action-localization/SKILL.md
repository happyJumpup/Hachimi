---
name: visual-action-localization
version: 1.4.1
description: Localize all exercise demonstrations in one continuous video chunk.
---

# Visual action localization

## Input

A continuous video clip plus JSON metadata describing its clip-local timeline.
The clip is one overlapping chunk of a controlled source video. The caller,
not the model, converts returned times first to the analysis-range clock; the
run manager later adds the source-range offset exactly once.

## Output

Return JSON only:

```json
{
  "segments": [
    {
      "action_name": "string or null",
      "start_seconds": 0,
      "end_seconds": 0,
      "visual_cue": "short observable cue",
      "segment_role": "follow_along | teaching_demo | unknown"
    }
  ]
}
```

## Rules

- Output clip-local seconds within the supplied timeline. Never add or guess a
  source-video offset.
- Inspect the complete continuous video clip and identify every observable
  exercise demonstration, keeping segments in timeline order.
- Merge consecutive moments showing the same exercise into one segment.
- Do not claim precision beyond what is visually observable.
- Keep an unknown action name as `null`; do not guess from appearance alone.
- Set `segment_role` only from observable timeline evidence: use `follow_along`
  for a sustained interval presented for synchronous execution,
  `teaching_demo` for explanation, demonstration, or correction, and `unknown`
  whenever the role is not visually clear.
- Never convert the elapsed length of a teaching demonstration into a training
  duration.
- Do not infer body data, load, injury risk, exercise quality, or effectiveness.
- Visual cues describe visible evidence, not hidden reasoning.
