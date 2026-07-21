---
name: visual-action-localization
version: 1.2.0
description: Localize all exercise demonstrations in a controlled full-source video.
---

# Visual action localization

## Input

A row-major contact sheet sampled uniformly across the controlled full source,
plus JSON metadata containing each frame's absolute timestamp and the full
source range.

## Output

Return JSON only:

```json
{
  "segments": [
    {
      "action_name": "string or null",
      "start_seconds": 0,
      "end_seconds": 0,
      "visual_cue": "short observable cue"
    }
  ]
}
```

## Rules

- Output absolute source-video times within the supplied window.
- Inspect the complete contact-sheet timeline and identify every observable
  exercise demonstration, keeping segments in timeline order.
- Merge consecutive sampled frames showing the same exercise into one segment;
  never emit one segment per frame.
- Use adjacent sampled-frame timestamps to estimate boundaries. Do not claim
  sub-frame precision.
- Keep an unknown action name as `null`; do not guess from appearance alone.
- Do not infer body data, load, injury risk, exercise quality, or effectiveness.
- Visual cues describe visible evidence, not hidden reasoning.
